"""The KMS key the session keys are wrapped under outlives every environment.

Each session's content, in the database, its backups, and a final snapshot,
is sealed under a data key that only this key unwraps, so destroying the
key destroys that content for good. So the key is the account's, in the
bootstrap root a person applies, and never in an environment's graph: a
destroy of an environment, the nuke's included, cannot reach it, no deploy
run may destroy or replace it, and the nuke names it among what remains.
Read from the Terraform and the script as text; no cloud is needed."""

import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TERRAFORM = ROOT / "deployment" / "terraform"
MODULES = TERRAFORM / "modules"
NUKE = ROOT / "scripts" / "cloud_nuke.sh"

KMS_RESOURCE = re.compile(r'^resource "aws_kms_(key|alias|replica_key)"', re.MULTILINE)
DESTROYS_A_KEY = (
    "kms:CreateKey",
    "kms:DeleteAlias",
    "kms:DisableKey",
    "kms:PutKeyPolicy",
    "kms:ScheduleKeyDeletion",
    "kms:UpdateAlias",
)


def graph(folder: Path) -> set[Path]:
    """The folder and every module it calls, and every module those call."""
    found = {folder.resolve()}
    for tf in folder.glob("*.tf"):
        for source in re.findall(r'source\s*=\s*"(\.[^"]+)"', tf.read_text()):
            found |= graph(folder / source)
    return found


def block(text: str, header: str) -> str:
    """The block that opens with `header`, read by matching braces; a
    header inside a block, such as a statement's `sid`, names the block
    around it."""
    start = text.index(header)
    if not header.startswith(("resource ", "module ", "data ")):
        start = text.rindex("statement {", 0, start)
    depth = 0
    for end in range(text.index("{", start), len(text)):
        depth += {"{": 1, "}": -1}.get(text[end], 0)
        if depth == 0:
            return text[start : end + 1]
    raise AssertionError(f"{header} does not close")


def test_no_environment_graph_declares_a_kms_key() -> None:
    environments = sorted((TERRAFORM / "environments").iterdir())
    assert environments
    for environment in environments:
        for module in graph(environment):
            for tf in module.glob("*.tf"):
                assert not KMS_RESOURCE.search(tf.read_text()), f"{tf} declares a KMS key"


def test_the_account_root_holds_the_key_and_the_alias_the_processes_read() -> None:
    account = (MODULES / "account" / "main.tf").read_text()
    key = block(account, 'resource "aws_kms_key" "sessions"')
    assert "enable_key_rotation     = true" in key
    assert "deletion_window_in_days = 30" in key
    alias = block(account, 'resource "aws_kms_alias" "sessions"')
    assert 'name          = "alias/acme-${var.environment}-sessions"' in alias
    for bootstrap in sorted((TERRAFORM / "bootstrap").iterdir()):
        assert MODULES / "account" in graph(bootstrap), f"{bootstrap.name} has no account"
    keys = (MODULES / "keys" / "outputs.tf").read_text()
    assert 'value       = "alias/acme-${var.environment}-sessions"' in keys
    environment = (MODULES / "environment" / "main.tf").read_text()
    assert "ACME_KMS_KEY_ID          = module.keys.alias_name" in environment


def test_no_deploy_run_may_destroy_or_replace_the_key() -> None:
    """The deploy role's only KMS allows are reads, and its fence denies
    every call that destroys a key, disables it, or moves its alias."""
    deploy = (MODULES / "deploy_role" / "main.tf").read_text()
    fence = block(deploy, 'sid    = "NotWhatTheBootstrapOwns"')
    assert 'effect = "Deny"' in fence
    for action in DESTROYS_A_KEY:
        assert f'"{action}"' in fence, action
    allowed = set(re.findall(r'"(kms:[A-Za-z*]+)"', deploy.replace(fence, "")))
    assert allowed == {"kms:Describe*", "kms:List*"}


def test_the_task_roles_use_the_environments_keys_and_never_manage_them() -> None:
    use = (MODULES / "keys" / "main.tf").read_text()
    assert set(re.findall(r'"(kms:[A-Za-z*]+)"', use)) == {
        "kms:Decrypt",
        "kms:GenerateDataKey",
        "kms:ReEncryptFrom",
        "kms:ReEncryptTo",
    }
    assert 'variable = "aws:ResourceTag/acme:environment"' in use


def test_the_nuke_names_the_key_among_what_remains(tmp_path: Path) -> None:
    """Printed for every environment: the line stands outside every branch
    of the script, and the staging dry run shows it."""
    script = NUKE.read_text()
    (line,) = [s for s in script.splitlines() if "alias/acme-$environment-sessions" in s]
    assert line.startswith('say "- the KMS key'), "inside a branch"
    assert script.index(line) > script.index('say "== 5. What remains"')
    result = subprocess.run(
        ["bash", str(NUKE), "staging", "--dry-run"],
        capture_output=True,
        text=True,
        cwd=ROOT,
        env={
            "PATH": os.environ["PATH"],
            "HOME": str(tmp_path),
            "OWNER_EMAIL": "owner@acme.example",
            "ALARM_EMAIL": "alarms@acme.example",
        },
    )
    assert result.returncode == 0, result.stderr
    remains = result.stdout[result.stdout.index("== 5. What remains") :]
    assert "- the KMS key alias/acme-staging-sessions, in the bootstrap root" in remains
