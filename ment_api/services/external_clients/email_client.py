import requests

from ment_api.config import settings


def send_rejection_email(dest, ownerFirstName, rejectionReason):
    url = "https://api.sendgrid.com/v3/mail/send"
    # Constructing the data to send
    data = {
        "personalizations": [
            {
                "to": [{"email": dest}],
                "dynamic_template_data": {
                    "first_name": ownerFirstName,
                    "rejection_reason": rejectionReason,
                    "subject": "Your latest task has been rejected, please update",
                },
            },
        ],
        "from": {
            "email": "noreply@tryment.app",
            "name": "Ment",
        },
        "template_id": "d-88a56538a5fb4282a8212b1261537bcb",
    }

    headers = {
        "Authorization": f"Bearer {settings.send_grid_key}",
        "Content-Type": "application/json",
    }

    response = None
    response = requests.post(url, json=data, headers=headers)
    if response.status_code == 202:
        return True


def send_new_match_email_notification(dest):
    url = "https://api.sendgrid.com/v3/mail/send"
    data = {
        "personalizations": [
            {
                "to": [{"email": dest}],
                "dynamic_template_data": {
                    "buttonUrl": "https://tryment.app/user/tasks",
                    "subject": "You got a new match!",
                },
            },
        ],
        "from": {"email": "noreply@tryment.app", "name": "Ment"},
        "template_id": "d-a4dbd702e8c04d0eb10bc87a16c01a65",
        "asm": {"group_id": 24805, "groups_to_display": [24805]},
    }

    headers = {
        "Authorization": f"Bearer {settings.send_grid_key}",
        "Content-Type": "application/json",
    }
    response = None
    response = requests.post(url, json=data, headers=headers)
    if response.status_code == 202:
        return True
