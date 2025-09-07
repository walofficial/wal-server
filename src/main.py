import uvicorn
from ment_api.app import app
import google.auth
import logging
creds, project = google.auth.default(scopes=['https://www.googleapis.com/auth/cloud-platform'])
logging.info(
    "ADC resolved",
    extra={
        "json_fields": {
            "credentials_type": type(creds).__name__,
            "project": project,
            "operation": "adc_probe"
        },
        "labels": {"component": "ment_api"}
    }
)
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=True)
