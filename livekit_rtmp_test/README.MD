# LiveKit RTMP Test

Files in this folder are used to:

1. Create LiveKit RTMP ingress endpoints that accept streams from:

   - DJI hardware
   - OBS software
   - Mobile app RTMP publishers

2. Set up egress to record those streams to cloud storage

The main purpose is testing RTMP publishing from the mobile app.

### Simulating Livekit Ingress with DJI

1. Create a ingress with UI or calling endpoint /request-livekit-ingress
2. Put the StreamAPI Key and URL in the drone and start recording.
3. Webhook should call appropriate endpoint.
