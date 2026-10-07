# ADR 1021: A host directory runs its commands as an account of its own, one workspace at a time

**Status**: accepted (2026-10-07)

## Context

The host mode is a directory on this host. A directory confines where
files go and nothing else, so the host provider refuses every limit,
and a command there runs as this process: it reaches this process's
files, its credential, the other workspaces, and the records. A
container holds more, but it needs a container runtime, which a machine
the engine runs on may not have, or may not allow.

An account of the host is the wall such a machine has. A command that
runs as an account of its own, with none of this process's privileges,
reaches what the account may and nothing else. Two things make that
wall hard to hold. The switch takes privileges, and a privileged
program that loads what a command chose hands them over. And an
account's processes are no tree: one a command left in a session of its
own outlives the command's process group.

## Decision

**A mode of its own.** `account` is an isolation mode beside `host`: a
directory on the host whose commands run as a named account. A spec
asks for it by name, so a session that asks for an account's
containment is never met by a plain directory.

**The switch.** Each command runs as the account, with its group, no
supplementary group, every capability dropped from every set, and
`no_new_privs` (`setpriv`), in a session of its own. Its limits are
lowered before the switch, hard and soft (`prlimit`), so it cannot
raise them again. The mode enforces `processes`, the account's, which
are the workspace's alone. A share of the cpus and a bound on memory
are the whole workspace's, which no limit of one process holds, so a
spec that asks for either is refused, and so is any egress but open.
Prepare refuses a host that cannot switch: not Linux, no `setpriv` or
`prlimit`, an account that is missing, root's, or this process's own, a
process outside the account's group, or one without the capabilities
the switch takes. It refuses a host that lets an account link a file it
does not own (`fs.protected_hardlinks`), too.

**Nothing privileged sees the command's environment.** The programs
that switch run with an empty environment, since a variable such as
`LD_PRELOAD` would load the account's code into them. The command's
environment goes down a pipe on its standard input, and a shell reads
it after the switch. It is built from nothing, as the host mode's is,
with the command's home and temporary directory in the workspace's
directory. No value goes on a command line, which every account of the
host can list. The command's directory is entered after the switch, as
the account, so a link there reaches only what the account may.

**One workspace at a time.** The account serves one workspace at a
time, across every process that shares the root: a lock beside the
workspaces holds it, and a prepare while another workspace holds it is
refused. So every process of the account is that workspace's. A release
ends each of them, then those in its directory, and closes the
workspace's directory to the account, so the next workspace's commands
never reach its files. A fresh hold first ends what a crash left of the
account, and closes the directory of the workspace it served last.
The account's processes are ended as the account (`kill -KILL -1`),
which reaches each one, whatever `/proc` hides from this process, and
which no fork escapes. The end is repeated until the account's own view
of `/proc` shows none alive, or it fails.

**This process follows no link there.** It reads, writes, and lists in
the workspace with more than the account may. So a path is walked one
step at a time, never through a link, to a regular file of the account
or of this process, never one of another owner that a hard link
reaches, and never one of this process's with a second link.

**A purge clears as the account.** A purge ends the account's
processes, clears what the account wrote, as the account, its own
directories opened to it first, then removes what is left without
following a link out of the directory. While the account serves
another workspace, a purge removes only what this process can, and
fails on the rest, to be tried again.

**What stays a product's.** Which account a host gives its agents, what
it grants that account (a device, a group, a socket family, a
scheduling policy), and how the host's image makes it. The account is
the workspaces' alone: a process of it the engine did not start is
ended as a leftover.

## Consequences

- On a host with no container runtime, code an agent runs reaches
  nothing of this process's: not its files, its credential, its
  environment, the records, or another workspace.
- A process a command left running, in its session or out of it, ends
  with the workspace's release.
- One account runs one workspace at a time. A second session that asks
  for the mode while the first holds it is refused, and a tree of
  agents that each need a workspace needs a provider and an account for
  each.
- The mode runs on Linux alone, by a process that holds `CAP_SETUID`,
  `CAP_SETGID`, `CAP_SETPCAP`, and `CAP_KILL` and is in the account's
  group, under a root the account passes through. Its tests are skipped
  elsewhere, with the reason.
- A deployed environment refuses the backend, as it refuses the host
  one: a deployed process runs tools in containers, or none.
- What the account closed even to itself stays until a purge holds the
  account.
