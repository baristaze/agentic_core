"""The example assistant: a kind whose loop runs in the request that asks
it (ADR 1044), answering a member's questions about the org's files
through one read tool, which no person approves. A person starts it; no
agent does."""

from acme.om.agent_sessions.limits import Limits
from acme.om.agents.prompts import prompt_of
from acme.om.agents.types.kind import REQUEST_STEPS, REQUEST_TIME, AgentKind, DoneRule, RunsIn
from acme.om.attribution.types.authority import AuthorityMode
from acme.om.tools.types.policy import Decision, ToolPolicyLayer, ToolPolicyRule
from acme.om.tools.types.tool import ToolClass
from acme.services.api.list_files import LIST_FILES

ASSISTANT = AgentKind(
    name="assistant",
    version=1,
    tools=(LIST_FILES,),
    done_rule=DoneRule.ANSWER,
    authority=AuthorityMode.DELEGATED,
    prompts=(prompt_of(__file__, 1),),
    policy=ToolPolicyLayer(
        rules=(ToolPolicyRule(authorization_class=ToolClass.READ, decision=Decision.ALLOW),)
    ),
    limits=Limits(step_guard=REQUEST_STEPS, run_time=REQUEST_TIME),
    runs_in=RunsIn.REQUEST,
)

VERSIONS = (ASSISTANT,)
"""Every version of the assistant the example still runs."""
