# Backend for MENT | Gemini Competition | MENT by Movement

## requirements:

- docker

run main backend server:

`docker compose -f docker-compose.dev.yml up --build`

## notes

- to read current user, all user endpoints have user_id in the header which is set by Cloudflare Worker after user is authorized.
