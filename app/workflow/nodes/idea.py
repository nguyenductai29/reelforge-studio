"""Idea: the project's title and topic, resolved locally."""
from app.workflow.nodes.base import NodeHandler
from app.workflow.ports import BRIEF, OutputPort
from app.workflow.results import NodeExecutionResult


class IdeaNodeHandler(NodeHandler):
    node_type = "idea"
    # "topic" falls back to the title when the project has no topic.
    outputs = (OutputPort("topic", BRIEF, keys=("topic", "title")), OutputPort("title", BRIEF))

    def execute(self, context, node, inputs):
        project = context.project
        if project.topic.strip() or project.title.strip():
            return NodeExecutionResult.completed("Đã lấy ý tưởng từ dự án.",
                                                 {"title": project.title, "topic": project.topic})
        return NodeExecutionResult.blocked("Dự án chưa có nội dung ý tưởng.")
