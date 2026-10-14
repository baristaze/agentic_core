"""The tools the engine ships. A session's registry holds those its kind
names, beside the adopter's own."""

from collections.abc import Callable

from acme.om.agent_sessions import AgentSessionsManagerInterface
from acme.om.agent_steps import AgentStepsManagerInterface
from acme.om.agents import AgentsManagerInterface
from acme.om.tools.attachments import AttachmentReaderInterface
from acme.om.tools.native.ask_person import AskPersonToolImpl
from acme.om.tools.native.load_toolset import LoadToolsetToolImpl
from acme.om.tools.native.read_attachment import ReadAttachmentToolImpl
from acme.om.tools.native.read_tool_output import ReadToolOutputToolImpl
from acme.om.tools.native.spawn_sub_agent import SpawnSubAgentToolImpl
from acme.om.tools.native.wait_for_sub_agents import WaitForSubAgentsToolImpl
from acme.om.tools.native.write_agent_plan import WriteAgentPlanToolImpl
from acme.om.tools.tool import ToolInterface
from acme.om.windows import WindowsManagerInterface


def native_tools(
    steps: AgentStepsManagerInterface,
    sessions: AgentSessionsManagerInterface,
    attachments: AttachmentReaderInterface,
    windows: WindowsManagerInterface,
    agents: Callable[[], AgentsManagerInterface],
) -> tuple[ToolInterface, ...]:
    """The tools the engine ships, offered to a session only when its kind
    names them: asking its person or standing down, writing its plan,
    reading an attachment by range, reading a result kept whole outside the
    window by the handle the window names, starting a sub-agent through the
    agents manager `agents` provides, and waiting on its sub-agents. A kind
    that defers a toolset holds the seventh, loading it."""
    return (
        AskPersonToolImpl(),
        WriteAgentPlanToolImpl(),
        LoadToolsetToolImpl(),
        ReadAttachmentToolImpl(steps, attachments),
        ReadToolOutputToolImpl(steps, windows),
        SpawnSubAgentToolImpl(sessions, agents),
        WaitForSubAgentsToolImpl(sessions),
    )
