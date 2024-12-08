import requests
from ment_api.config import settings


def clear_all_user_caches_from_cloudflare_kv():
    # Prod namespace
    # IMPORTANT: this is different cloudflare account
    namespace_id = "30efc7e02c764ed6bb2d6ffc5020ea63"
    account_id = "b22674a62c95d989a7ff04fe5827889c"
    auth_key = "4552c4fc7b302b7bd0bab0f2894d7f6b04ee4"

    list_keys_url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/storage/kv/namespaces/{namespace_id}/keys"
    headers = {
        "X-Auth-Email": "nshprimary@gmail.com",
        "X-Auth-Key": auth_key,
    }

    response = requests.get(list_keys_url, headers=headers)

    if response.status_code != 200:
        raise Exception("Couldn't retrieve keys")

    keys = response.json().get("result", [])

    for key in keys:
        user_id = key["name"]

        delete_url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/storage/kv/namespaces/{namespace_id}/values/{user_id}"
        delete_response = requests.delete(delete_url, headers=headers)

        if delete_response.status_code != 200:
            raise Exception(f"Couldn't clear cache for user {user_id}")
        else:
            print(f"Cache cleared for user {user_id}")

    return {"status": "All user caches cleared"}


def upload_video_to_cloudflare_stream(file_content: bytes, file_name: str):

    api_url = f"https://api.cloudflare.com/client/v4/accounts/{settings.cloudflare_account_id}/stream"

    headers = {
        "Authorization": f"Bearer " + settings.cloudflare_api_token,
    }

    files = {
        "file": (
            file_name,
            file_content,
        ),
    }

    response = requests.post(api_url, headers=headers, files=files)

    if response.status_code == 200:
        result = response.json().get("result", {})
        return {
            "status": "success",
            "video_id": result.get("uid"),
            "playback": result.get("playback"),
            "thumbnail_url": result.get("thumbnail"),
        }
    else:
        return {
            "status": "error",
            "message": f"Failed to upload video. Status code: {response.status_code}",
            "details": response.json(),
        }
