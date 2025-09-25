import logging
import os
import uuid
from typing import Any, Optional

import httpx
from a2a.client import A2ACardResolver, A2AClient  # type: ignore
from a2a.types import (  # type: ignore
    DataPart,
    MessageSendParams,
    SendMessageRequest,
    SendMessageResponse,
    SendMessageSuccessResponse,
    Task,
)

from ment_api.models.fact_checking_models import (
    FactCheckingResult,
    FactCheckRequest,
    JinaFactCheckResponse,
)
from ment_api.services.external_clients.langfuse_client import langfuse
from ment_api.services.fact_checking_service import (
    create_fact_checking_prompt,
)

logger = logging.getLogger(__name__)


class A2AFactCheckAgent:
    """Simplified A2A fact-checking agent client."""

    def __init__(self, agent_url: str):
        self.agent_url = agent_url
        self.token = os.getenv("TOKEN")
        self.headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}

    async def _get_client(self) -> A2AClient:
        """Get initialized A2A client."""
        async with httpx.AsyncClient(timeout=1000) as client:
            resolver = A2ACardResolver(client, self.agent_url)
            agent_card = await resolver.get_agent_card()

        httpx_client = httpx.AsyncClient(timeout=1000, headers=self.headers)
        a2a_client = A2AClient(httpx_client, agent_card, url=self.agent_url)

        logger.info(
            "A2A fact-check agent initialized",
            extra={
                "json_fields": {
                    "agent_name": getattr(agent_card, "name", None),
                    "agent_url": self.agent_url,
                    "operation": "a2a_agent_initialized",
                },
                "labels": {"component": "a2a_fact_checker", "phase": "init"},
            },
        )

        return a2a_client

    async def check_fact(
        self, request: FactCheckRequest
    ) -> Optional[FactCheckingResult]:
        """Check a statement for factual accuracy."""
        client = await self._get_client()

        prompt = create_fact_checking_prompt(request.details)
        message_id = uuid.uuid4().hex

        payload: dict[str, Any] = {
            "message": {
                "role": "user",
                "parts": [{"type": "text", "text": prompt}],
                "messageId": message_id,
            }
        }

        try:
            with langfuse.start_as_current_generation(
                name="a2a_fact_check",
                model="gemini-2.5-flash",
            ) as gen:
                gen.update(
                    input={
                        "prompt": prompt,
                        "statement": request.details,
                        "model_config": {
                            "model": "gemini-2.5-flash",
                            "service": "a2a-remote-agent",
                            "timeout_seconds": 600,
                        },
                    },
                    metadata={
                        "verification_id": str(request.verification_id),
                        "operation_type": "fact_check_generation",
                        "provider": "a2a",
                    },
                )

                message_request = SendMessageRequest(  # type: ignore[call-arg]
                    id=message_id,
                    params=MessageSendParams.model_validate(payload),  # type: ignore[arg-type]
                )

                response: SendMessageResponse = await client.send_message(
                    message_request
                )

                model: Optional[JinaFactCheckResponse] = None
                raw_response: Optional[dict] = None

                match response.root:
                    case SendMessageSuccessResponse() as success_response:
                        match success_response.result:
                            case Task() as task:
                                artifacts = task.artifacts or []
                                for artifact in reversed(artifacts):
                                    parts = artifact.parts or []
                                    for part in reversed(parts):
                                        match part.root:
                                            case DataPart() as data_part:
                                                raw_response = data_part.data.get(
                                                    "response"
                                                )
                                                model = JinaFactCheckResponse(
                                                    **raw_response
                                                )

                if not model:
                    return None

                result = FactCheckingResult(
                    factuality=model.factuality,
                    reason=model.reason,
                    score_justification=model.score_justification,
                    reason_summary=model.reason_summary,
                    references=model.references,
                    visited_urls=raw_response.get("visitedURLs", []),
                    read_urls=raw_response.get("readURLs", []),
                )

                gen.update(
                    output={
                        "factuality_score": result.factuality,
                        "reason": result.reason,
                        "reason_summary": result.reason_summary,
                        "references_count": len(result.references),
                    },
                    metadata={
                        "provider": "a2a",
                        "agent_url": self.agent_url,
                        "operation": "a2a_fact_check_success",
                    },
                )

                return result

        except Exception as e:
            logger.error(
                "A2A fact check failed",
                extra={
                    "json_fields": {
                        "verification_id": str(request.verification_id),
                        "error": str(e),
                        "error_type": type(e).__name__,
                        "operation": "a2a_fact_check_error",
                    },
                    "labels": {"component": "a2a_fact_checker", "severity": "high"},
                },
                exc_info=True,
            )
            return None


# Global agent instance
_fact_check_agent: Optional[A2AFactCheckAgent] = None


def _get_fact_check_agent() -> A2AFactCheckAgent:
    """Get or create the global fact-check agent instance."""
    global _fact_check_agent
    if _fact_check_agent is None:
        agent_url = os.getenv(
            "A2A_FACT_CHECK_AGENT_URL",
            "http://host.docker.internal:8080",
        )
        _fact_check_agent = A2AFactCheckAgent(agent_url)
    return _fact_check_agent


async def check_fact(request: FactCheckRequest) -> Optional[FactCheckingResult]:
    """Check a statement for factual accuracy using a remote A2A Agent.

    Expects the agent to return the same JSON schema as `JinaFactCheckResponse`.
    """
    agent = _get_fact_check_agent()
    return await agent.check_fact(request)
