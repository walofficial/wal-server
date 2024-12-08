import json

from ment_api.common.gpt_functions import gpt_function_process_user
from ment_api.common.gpt_prompts import get_user_generator_SYSYTEM_PROMPT
from ment_api.common.utils import add_system_message, add_user_message
from ment_api.services.external_clients.openai_client import aclient


def parse_function_call(message, fn_name: str):
    function_call = message.function_call
    if not function_call:
        raise Exception("No function call found")

    function_call_args = function_call.arguments
    if not function_call_args:
        raise Exception("No function call arguments found")

    if function_call.name == fn_name:
        return json.loads(function_call_args)

    raise Exception("Function call name does not match")


async def generate_user(is_georgian=False):
    user_prompt = generate_main_user_prompt()
    messages = [
        add_system_message(get_user_generator_SYSYTEM_PROMPT(is_georgian)),
        add_user_message(user_prompt),
    ]

    tools = [
        gpt_function_process_user(),
    ]

    response = await aclient.chat.completions.create(
        model="gpt-4o",
        functions=tools,
        function_call={
            "name": "process_user",
            "arguments": "generated_user",
        },
        messages=messages,
        max_tokens=4096,
        temperature=1,
        top_p=1,
    )

    response_message = response.choices[0].message
    if dict(response_message).get("function_call"):
        try:
            function_args = parse_function_call(response_message, "process_user")
            generated_user = function_args.get("generated_user")
            return generated_user
        except Exception as e:
            raise Exception("Cannot parse function call")


def generate_main_user_prompt():
    user_prompt = f"""
     Generate user and tasks with its verifications
    """
    return user_prompt
