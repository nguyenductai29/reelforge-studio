"""Idea: the project's title and topic, resolved locally."""
from app.workflow.nodes.base import NodeHandler
from app.workflow.results import NodeExecutionResult


class IdeaNodeHandler(NodeHandler):
    node_type = "idea"

    def execute(self, context, node, inputs):
        project = context.project
        if project.topic.strip() or project.title.strip():
            return NodeExecutionResult.completed("Đã lấy ý tưởng từ dự án.",
                                                 {"title": project.title, "topic": project.topic})
        return NodeExecutionResult.blocked("Dự án chưa có nội dung ý tưởng.")
