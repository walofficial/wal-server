def gpt_function_process_interests():
    return {
        "name": "process_interests",
        "description": "Responsible to process interests",
        "parameters": {
            "type": "object",
            "properties": {
                "generated_interests": {
                    "type": "array",
                    "description": "array of generated interests",
                    "items": {
                        "type": "string",
                    },
                }
            },
        },
    }


task_object = {
    "taskTitle": {
        "type": "string",
        "description": "title of the task",
    },
    "taskType": {
        "type": "string",
        "description": "type of the task",
    },
    "taskVerifications": {
        "type": "array",
        "description": "Single element array of how task verification should be done",
        "items": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Name of the verification (e.g., Verify by taking a photo at McLaren studio, Verify by guessing user answer)",
                },
                "requirements": {
                    "type": "array",
                    "description": "Requirements for the verification (e.g., camera, text)",
                    "items": {"type": "string"},
                },
            },
        },
    },
}


def gpt_function_process_tasks():
    return {
        "name": "process_tasks",
        "description": "Responsible to process tasks",
        "parameters": {
            "type": "object",
            "properties": {
                "generated_tasks": {
                    "type": "array",
                    "description": "array of generated tasks",
                    "items": {"type": "object", "properties": task_object},
                },
            },
        },
    }


def gpt_function_process_user():
    return {
        "name": "process_user",
        "description": "Responsible to process user",
        "parameters": {
            "type": "object",
            "properties": {
                "generated_user": {
                    "type": "object",
                    "properties": {
                        "first_name": {
                            "type": "string",
                            "description": "first name of the user",
                        },
                        "last_name": {
                            "type": "string",
                            "description": "last name of the user",
                        },
                        "username": {
                            "type": "string",
                            "description": "username of the user",
                        },
                        "email": {
                            "type": "string",
                            "description": "email address of the user",
                        },
                        "profile_image": {
                            "type": "string",
                            "description": "URL of the user's profile image",
                        },
                        "photo_prompts": {
                            "type": "array",
                            "description": "array of users profile photo prompts for stable diffusion image generation, each prompt should include age of the user eg 21, 25, 30",
                            "items": {
                                "type": "string",
                            },
                        },
                        "interests": {
                            "type": "array",
                            "description": "array of user's interests",
                            "items": {
                                "type": "string",
                            },
                        },
                        "date_of_birth": {
                            "type": "string",
                            "description": "date of birth of the user",
                        },
                        "gender": {
                            "type": "string",
                            "description": "gender of the user",
                        },
                        "full_name": {
                            "type": "string",
                            "description": "name of the user",
                        },
                        "gender_preference": {
                            "type": "string",
                            "description": "user's gender preference",
                        },
                    },
                },
            },
        },
    }
