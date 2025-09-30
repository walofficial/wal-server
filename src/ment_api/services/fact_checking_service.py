import logging
from datetime import datetime

logger = logging.getLogger(__name__)


# Create fact checking prompt
def create_fact_checking_prompt(details: str) -> str:
    current_date = datetime.now().strftime("%Y-%m-%d")

    logger.debug(
        "Creating fact checking prompt",
        extra={
            "json_fields": {
                "details_length": len(details),
                "current_date": current_date,
                "base_operation": "fact_check",
                "operation": "jina_prompt_creation",
            },
            "labels": {"component": "jina_fact_checker", "phase": "prompt"},
        },
    )
    prompt = f"""
<details>
{details}
</details>
"""

    logger.debug(
        "Fact checking prompt created",
        extra={
            "json_fields": {
                "prompt_length": len(prompt),
                "details_length": len(details),
                "base_operation": "fact_check",
                "operation": "jina_prompt_created",
            },
            "labels": {"component": "jina_fact_checker", "phase": "prompt"},
        },
    )

    return prompt


