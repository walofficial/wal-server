from ment_api.common.dummy import georgian_woman_names


def get_tasks_enhancer_SYSTEM_PROMPT():
    return f"""
You are an helpful assistant who is tasked to verify and check if task verifications details are necessary for task in a tinder like application and check the task title and see if it's okay for it to be published based on criteria: no rude words or harassment or bullying
Tasks are engaging and feasible tasks that can be included in a Tinder-like web app for teenagers. In this app, users can swipe left or right on profiles featuring tasks instead of just profile pictures and names. Each task should be fun, encourage social interaction, and be suitable for teenagers. The tasks should also be verifiable through images or simple text based answers.
Now you are given a task title which was inputted by the user so that it can be completed by other users.

Let's think step by step to validate task title and generate verifications:

1. To verify task, title shouldn't be more than 20 words long
2. You should only generate verifications for tasks which can be verified with action such as camera photo, or just answering with text.
3. Check task title and make sure it's easy to do for average human being or teenager
4. Task title doesn't contain something which means paying a lot of money, like check-in in hotel
5. Verify the task title and make sure it's valid based on following: not rude words, sexual harassment or bullying.


Task examples which can be just verified with text answer 
1. Guess my favorite food
2. Guess my favorite book genre.

Make sure to allow all tasks which not include: rude words, sexual harassment or bullying.
Wait when prompted to validate task and generate verifications

"""


def get_user_generator_SYSYTEM_PROMPT(isGeorgian=False):
    return f"""
You are an helpful assistant who is tasked to generate user for a tinder like application.

Generate a user for a Tinder-like application. Follow these steps:

Generate first name, last name, email, preference, gender, and image prompts for stable diffusion model, including age in the prompts (e.g., "21-year-old girl").
Generate interests for the user.
Create a non-consecutive ID with characters and numbers.
Set the year of birth to any of 2003, 2004, 2005, or 2006.
Ensure that task verification is only possible with camera or text answer. not video.
Image prompts should specify a portrait or selfie photo of the girl.
Set the gender to both female and male.
Ensure the age of the girl is at least 25 years and looks like a average looking city girl.
Format the date of birth as DD/MM/YYYY.
{"Generate the georgian person" if isGeorgian else ""}
{"Georgian woman names " + ", ".join(georgian_woman_names) if isGeorgian else ""}
Additionally, generate a task and its details. The Tinder-like web app should display user profiles with a task title on each profile. Tasks can be simple and suitable for teenagers.

Example photo prompts, pick one of the examples it can be random:
{get_random_photo_prompt_example()}
{get_random_photo_prompt_example()}

{"Generate the user name in Georgian and task in Georgian" if isGeorgian else ""}
You can generate male users too, do not generate female users only.
"""


def get_random_photo_prompt_example():
    random_prompts = [
        "Boring Snapchat Photo of a young woman with long brown hair, wearing a casual summer dress, smiling at the camera in a park setting. The photo was shot on a phone and posted in 2015 on Snapchat.",
        "Casual Snapchat Photo of a woman in her early twenties with short blonde hair, wearing a leather jacket and a white t-shirt, leaning against a graffiti-covered wall. The photo was shot on a phone and posted in 2016 on Snapchat.",
        "Ordinary Snapchat Photo of a woman in her late twenties, with curly red hair, wearing glasses and a business suit, smiling warmly, indoors with a blurred office background. The photo was shot on a phone and posted in 2017 on Snapchat.",
        "Mundane Snapchat Photo of a young woman with straight black hair, wearing a colorful sundress, sitting at a coffee shop table. The photo was shot on a phone and posted in 2014 on Snapchat.",
        "Unexciting Snapchat Photo of a woman with wavy auburn hair, wearing a casual t-shirt and jeans, standing on a beach with the ocean in the background. The photo was shot on a phone and posted in 2018 on Snapchat.",
        "Basic Snapchat Photo of a young woman with short brunette hair, wearing a stylish hat and a denim jacket, standing in front of a city skyline. The photo was shot on a phone and posted in 2015 on Snapchat.",
        "Dull Snapchat Photo of a woman with long blonde hair, wearing a workout outfit, standing in a gym setting with fitness equipment in the background. The photo was shot on a phone and posted in 2016 on Snapchat.",
        "Plain Snapchat Photo of a woman with curly black hair, wearing a bohemian-style dress and accessories, sitting in a cozy, plant-filled living room. The photo was shot on a phone and posted in 2017 on Snapchat.",
        "Unremarkable Snapchat Photo of a young woman with straight red hair, wearing a formal evening dress, standing at a rooftop party with city lights in the background. The photo was shot on a phone and posted in 2018 on Snapchat.",
        "Simple Snapchat Photo of a woman with shoulder-length brown hair, wearing a casual sweater and scarf, standing in an autumn forest with colorful leaves around. The photo was shot on a phone and posted in 2015 on Snapchat.",
        "Boring Snapchat Photo of an athletic woman with dark skin, wearing a tank top and running shorts, standing on a track field. The photo was shot on a phone and posted in 2016 on Snapchat.",
        "Ordinary Snapchat Photo of a young woman with long, straight black hair, wearing a traditional sari, standing in front of a temple. The photo was shot on a phone and posted in 2017 on Snapchat.",
        "Mundane Snapchat Photo of a middle-aged woman with silver-gray hair, wearing a chic blouse and jeans, sitting on a park bench, holding a book. The photo was shot on a phone and posted in 2014 on Snapchat.",
        "Unexciting Snapchat Photo of a woman with a shaved head, wearing bold makeup and a punk rock outfit, standing in front of a music venue. The photo was shot on a phone and posted in 2018 on Snapchat.",
        "Basic Snapchat Photo of a young woman with medium-length curly hair, wearing a casual hijab and a stylish dress, standing in an urban street. The photo was shot on a phone and posted in 2015 on Snapchat.",
        "Dull Snapchat Photo of a woman with dreadlocks, wearing a colorful African print dress, standing in a vibrant market. The photo was shot on a phone and posted in 2016 on Snapchat.",
        "Plain Snapchat Photo of a young woman with freckles and wavy auburn hair, wearing a vintage dress, standing in a flower field. The photo was shot on a phone and posted in 2017 on Snapchat.",
        "Unremarkable Snapchat Photo of a woman in her thirties with a professional updo, wearing a blazer and holding a cup of coffee, standing in a modern office lobby. The photo was shot on a phone and posted in 2018 on Snapchat.",
        "Simple Snapchat Photo of a young woman with dyed blue hair, wearing trendy glasses and a graphic tee, standing in a cozy bookstore. The photo was shot on a phone and posted in 2015 on Snapchat.",
        "Boring Snapchat Photo of a woman with a tattoo sleeve, wearing a tank top and ripped jeans, sitting on a motorcycle. The photo was shot on a phone and posted in 2016 on Snapchat.",
        "Ordinary Snapchat Photo of a teenage girl with long brown hair, wearing a casual hoodie and jeans, sitting on a bench in a high school courtyard. The photo was shot on a phone and posted in 2017 on Snapchat.",
        "Mundane Snapchat Photo of a teenage girl with short blonde hair, wearing a graphic tee and denim jacket, standing in front of a colorful mural. The photo was shot on a phone and posted in 2014 on Snapchat.",
        "Unexciting Snapchat Photo of a teenage girl with curly red hair, wearing a trendy crop top and skirt, sitting in a modern café with a smoothie. The photo was shot on a phone and posted in 2018 on Snapchat.",
        "Basic Snapchat Photo of a teenage girl with straight black hair, wearing a casual sundress and sneakers, standing in a vibrant amusement park with a Ferris wheel in the background. The photo was shot on a phone and posted in 2015 on Snapchat.",
        "Dull Snapchat Photo of a teenage girl with wavy auburn hair, wearing a cozy sweater and jeans, sitting on a swing in a neighborhood park. The photo was shot on a phone and posted in 2016 on Snapchat.",
        "Plain Snapchat Photo of a teenage girl with short brunette hair, wearing a stylish hat and overalls, standing in a sunflower field. The photo was shot on a phone and posted in 2017 on Snapchat.",
        "Unremarkable Snapchat Photo of a teenage girl with long blonde hair, wearing sporty leggings and a tank top, standing in a school gym with sports equipment in the background. The photo was shot on a phone and posted in 2018 on Snapchat.",
        "Simple Snapchat Photo of a teenage girl with curly black hair, wearing a boho-chic dress and accessories, sitting on a blanket in a grassy field during a music festival. The photo was shot on a phone and posted in 2015 on Snapchat.",
        "Boring Snapchat Photo of a teenage girl with straight red hair, wearing a trendy jacket and jeans, standing on a busy urban street with bright lights and shops. The photo was shot on a phone and posted in 2016 on Snapchat.",
        "Ordinary Snapchat Photo of a teenage girl with shoulder-length brown hair, wearing a casual hoodie and headphones, sitting on a skateboard in an urban skate park. The photo was shot on a phone and posted in 2017 on Snapchat.",
        "Mundane Snapchat Photo of a teenage girl with dark skin and braided hair, wearing a colorful top and shorts, standing in front of an ice cream truck, holding an ice cream cone. The photo was shot on a phone and posted in 2014 on Snapchat.",
        "Unexciting Snapchat Photo of a teenage girl with straight black hair, wearing a traditional school uniform, standing in front of a historic building. The photo was shot on a phone and posted in 2018 on Snapchat.",
        "Basic Snapchat Photo of a teenage girl with medium-length curly hair, wearing a casual hijab and a stylish dress, standing in a busy shopping mall. The photo was shot on a phone and posted in 2015 on Snapchat.",
        "Dull Snapchat Photo of a teenage girl with freckles and wavy auburn hair, wearing a vintage band tee and jeans, sitting on a picnic blanket in a park. The photo was shot on a phone and posted in 2016 on Snapchat.",
        "Plain Snapchat Photo of a teenage girl with a shaved head, wearing bold makeup and a trendy jacket, standing in front of a concert venue. The photo was shot on a phone and posted in 2017 on Snapchat.",
        "Unremarkable Snapchat Photo of a teenage girl with dyed blue hair, wearing trendy glasses and a graphic tee, standing in a cozy bookstore. The photo was shot on a phone and posted in 2018 on Snapchat.",
        "Simple Snapchat Photo of a teenage girl with a tattoo sleeve, wearing a tank top and ripped jeans, sitting on a beach with friends in the background. The photo was shot on a phone and posted in 2015 on Snapchat.",
        "Boring Snapchat Photo of a teenage girl with long black hair, wearing a cute romper and sandals, standing in a flower garden. The photo was shot on a phone and posted in 2016 on Snapchat.",
        "Ordinary Snapchat Photo of a teenage girl with a ponytail and a sporty outfit, standing on a basketball court with a basketball. The photo was shot on a phone and posted in 2017 on Snapchat.",
        "Mundane Snapchat Photo of a teenage girl with long blonde hair, wearing a fashionable dress and heels, standing at a school dance with colorful lights in the background. The photo was shot on a phone and posted in 2014 on Snapchat.",
        "Unexciting Snapchat Photo of a woman in her late twenties with long brown hair, wearing a business casual outfit, standing in a modern office space with a laptop in hand. The photo was shot on a phone and posted in 2018 on Snapchat.",
        "Basic Snapchat Photo of a woman in her early thirties with short blonde hair, wearing a chic cocktail dress, standing at a rooftop bar with city lights in the background, holding a glass of wine. The photo was shot on a phone and posted in 2015 on Snapchat.",
        "Dull Snapchat Photo of a woman in her forties with curly red hair, wearing a cozy sweater and jeans, sitting on a park bench with autumn leaves around. The photo was shot on a phone and posted in 2016 on Snapchat.",
        "Plain Snapchat Photo of a woman in her mid-thirties with straight black hair, wearing an elegant evening gown, standing at a formal event with a grand staircase in the background. The photo was shot on a phone and posted in 2017 on Snapchat.",
        "Unremarkable Snapchat Photo of a woman in her fifties with gray hair, wearing a stylish blouse and trousers, sitting in a well-lit café with a book and a cup of coffee. The photo was shot on a phone and posted in 2018 on Snapchat.",
    ]

    # INSERT_YOUR_CODE
    import random

    return random.choice(random_prompts)
