import asyncio
import copy
import logging
from typing import Optional, List, Tuple

from google.cloud.video.transcoder_v1 import TranscoderServiceClient, ElementaryStream
from google.cloud.video import transcoder_v1
from google.cloud.video.transcoder_v1.services.transcoder_service import (
    TranscoderServiceClient,
)
from ment_api.config import settings

logger = logging.getLogger(__name__)

client = TranscoderServiceClient()

transcoder_config_default = {
    "elementary_streams": [
        {
            "key": "video_stream0",
            "video_stream": {
                "h264": {
                    # "height_pixels": 96,
                    # "width_pixels": 170,
                    "bitrate_bps": 200000,
                    "frame_rate": 15,
                    "gop_duration": "4.0s",
                    "pixel_format": "yuv420p",
                    "rate_control_mode": "vbr",
                    "crf_level": 10,
                    "b_frame_count": 3,
                    "profile": "main",
                    "enable_two_pass": True,
                    "preset": "medium",
                }
            },
        },
        {
            "key": "video_stream1",
            "video_stream": {
                "h264": {
                    # "height_pixels": 144,
                    # "width_pixels": 256,
                    "bitrate_bps": 300000,
                    "frame_rate": 30,
                    "gop_duration": "4.0s",
                    "pixel_format": "yuv420p",
                    "rate_control_mode": "vbr",
                    "crf_level": 10,
                    "b_frame_count": 3,
                    "profile": "main",
                    "enable_two_pass": True,
                    "preset": "medium",
                }
            },
        },
        {
            "key": "video_stream2",
            "video_stream": {
                "h264": {
                    # "height_pixels": 234,
                    # "width_pixels": 416,
                    "bitrate_bps": 500000,
                    "frame_rate": 30,
                    "gop_duration": "4.0s",
                    "pixel_format": "yuv420p",
                    "rate_control_mode": "vbr",
                    "crf_level": 10,
                    "b_frame_count": 3,
                    "profile": "main",
                    "enable_two_pass": True,
                    "preset": "medium",
                }
            },
        },
        {
            "key": "video_stream3",
            "video_stream": {
                "h264": {
                    # "height_pixels": 360,
                    # "width_pixels": 640,
                    "bitrate_bps": 800000,
                    "frame_rate": 30,
                    "gop_duration": "4.0s",
                    "pixel_format": "yuv420p",
                    "rate_control_mode": "vbr",
                    "crf_level": 10,
                    "b_frame_count": 3,
                    "profile": "main",
                    "enable_two_pass": True,
                    "preset": "medium",
                }
            },
        },
        {
            "key": "video_stream4",
            "video_stream": {
                "h264": {
                    # "height_pixels": 432,
                    # "width_pixels": 768,
                    "bitrate_bps": 1200000,
                    "frame_rate": 30,
                    "gop_duration": "4.0s",
                    "pixel_format": "yuv420p",
                    "rate_control_mode": "vbr",
                    "crf_level": 10,
                    "b_frame_count": 3,
                    "profile": "main",
                    "enable_two_pass": True,
                    "preset": "medium",
                }
            },
        },
        {
            "key": "video_stream5",
            "video_stream": {
                "h264": {
                    # "height_pixels": 540,
                    # "width_pixels": 960,
                    "bitrate_bps": 1800000,
                    "frame_rate": 30,
                    "gop_duration": "4.0s",
                    "pixel_format": "yuv420p",
                    "rate_control_mode": "vbr",
                    "crf_level": 10,
                    "b_frame_count": 3,
                    "profile": "main",
                    "enable_two_pass": True,
                    "preset": "medium",
                }
            },
        },
        {
            "key": "video_stream6",
            "video_stream": {
                "h264": {
                    # "height_pixels": 1280,
                    # "width_pixels": 720,
                    "bitrate_bps": 2500000,
                    "frame_rate": 30,
                    "gop_duration": "4.0s",
                    "pixel_format": "yuv420p",
                    "rate_control_mode": "vbr",
                    "crf_level": 10,
                    "b_frame_count": 3,
                    "profile": "main",
                    "enable_two_pass": True,
                    "preset": "medium",
                }
            },
        },
        {
            "key": "video_stream7",
            "video_stream": {
                "h264": {
                    # "height_pixels": 1280,
                    # "width_pixels": 720,
                    "bitrate_bps": 4500000,
                    "frame_rate": 60,
                    "gop_duration": "4.0s",
                    "pixel_format": "yuv420p",
                    "rate_control_mode": "vbr",
                    "crf_level": 10,
                    "b_frame_count": 6,
                    "profile": "main",
                    "enable_two_pass": True,
                    "preset": "medium",
                }
            },
        },
        {
            "key": "video_stream8",
            "video_stream": {
                "h264": {
                    # "height_pixels": 1080,
                    # "width_pixels": 1920,
                    "bitrate_bps": 5000000,
                    "frame_rate": 30,
                    "gop_duration": "4.0s",
                    "pixel_format": "yuv420p",
                    "rate_control_mode": "vbr",
                    "crf_level": 10,
                    "b_frame_count": 3,
                    "profile": "main",
                    "enable_two_pass": True,
                    "preset": "medium",
                }
            },
        },
        {
            "key": "video_stream9",
            "video_stream": {
                "h264": {
                    # "height_pixels": 1080,
                    # "width_pixels": 1920,
                    "bitrate_bps": 7500000,
                    "frame_rate": 60,
                    "gop_duration": "4.0s",
                    "pixel_format": "yuv420p",
                    "rate_control_mode": "vbr",
                    "crf_level": 10,
                    "b_frame_count": 6,
                    "profile": "main",
                    "enable_two_pass": True,
                    "preset": "medium",
                }
            },
        },
        {
            "key": "audio_stream0",
            "audio_stream": {"codec": "aac", "bitrate_bps": 32000},
        },
        {
            "key": "audio_stream1",
            "audio_stream": {"codec": "aac", "bitrate_bps": 64000},
        },
        {
            "key": "audio_stream2",
            "audio_stream": {"codec": "aac", "bitrate_bps": 96000},
        },
        {
            "key": "audio_stream3",
            "audio_stream": {"codec": "aac", "bitrate_bps": 128000},
        },
        {
            "key": "audio_stream4",
            "audio_stream": {"codec": "aac-he", "bitrate_bps": 96000},
        },
        {
            "key": "audio_stream5",
            "audio_stream": {"codec": "aac-he-v2", "bitrate_bps": 128000},
        },
    ],
    "mux_streams": [
        {
            "key": "1",
            "container": "ts",
            "elementary_streams": ["video_stream0", "audio_stream0"],
            "segment_settings": {
                "segment_duration": "4.0s",
                "individual_segments": True,
            },
        },
        {
            "key": "2",
            "container": "ts",
            "elementary_streams": ["video_stream1", "audio_stream0"],
            "segment_settings": {
                "segment_duration": "4.0s",
                "individual_segments": True,
            },
        },
        {
            "key": "3",
            "container": "ts",
            "elementary_streams": ["video_stream2", "audio_stream1"],
            "segment_settings": {
                "segment_duration": "4.0s",
                "individual_segments": True,
            },
        },
        {
            "key": "4",
            "container": "ts",
            "elementary_streams": ["video_stream3", "audio_stream1"],
            "segment_settings": {
                "segment_duration": "4.0s",
                "individual_segments": True,
            },
        },
        {
            "key": "5",
            "container": "ts",
            "elementary_streams": ["video_stream4", "audio_stream2"],
            "segment_settings": {
                "segment_duration": "4.0s",
                "individual_segments": True,
            },
        },
        {
            "key": "6",
            "container": "ts",
            "elementary_streams": ["video_stream5", "audio_stream3"],
            "segment_settings": {
                "segment_duration": "4.0s",
                "individual_segments": True,
            },
        },
        {
            "key": "7",
            "container": "ts",
            "elementary_streams": ["video_stream6", "audio_stream3"],
            "segment_settings": {
                "segment_duration": "4.0s",
                "individual_segments": True,
            },
        },
        {
            "key": "8",
            "container": "ts",
            "elementary_streams": ["video_stream7", "audio_stream4"],
            "segment_settings": {
                "segment_duration": "4.0s",
                "individual_segments": True,
            },
        },
        {
            "key": "9",
            "container": "ts",
            "elementary_streams": ["video_stream8", "audio_stream4"],
            "segment_settings": {
                "segment_duration": "4.0s",
                "individual_segments": True,
            },
        },
        {
            "key": "10",
            "container": "ts",
            "elementary_streams": ["video_stream9", "audio_stream5"],
            "segment_settings": {
                "segment_duration": "4.0s",
                "individual_segments": True,
            },
        },
        {
            "key": "video_1",
            "container": "fmp4",
            "elementary_streams": ["video_stream0"],
            "segment_settings": {
                "segment_duration": "4.0s",
                "individual_segments": True,
            },
        },
        {
            "key": "video_2",
            "container": "fmp4",
            "elementary_streams": ["video_stream1"],
            "segment_settings": {
                "segment_duration": "4.0s",
                "individual_segments": True,
            },
        },
        {
            "key": "video_3",
            "container": "fmp4",
            "elementary_streams": ["video_stream2"],
            "segment_settings": {
                "segment_duration": "4.0s",
                "individual_segments": True,
            },
        },
        {
            "key": "video_4",
            "container": "fmp4",
            "elementary_streams": ["video_stream3"],
            "segment_settings": {
                "segment_duration": "4.0s",
                "individual_segments": True,
            },
        },
        {
            "key": "video_5",
            "container": "fmp4",
            "elementary_streams": ["video_stream4"],
            "segment_settings": {
                "segment_duration": "4.0s",
                "individual_segments": True,
            },
        },
        {
            "key": "video_6",
            "container": "fmp4",
            "elementary_streams": ["video_stream5"],
            "segment_settings": {
                "segment_duration": "4.0s",
                "individual_segments": True,
            },
        },
        {
            "key": "video_7",
            "container": "fmp4",
            "elementary_streams": ["video_stream6"],
            "segment_settings": {
                "segment_duration": "4.0s",
                "individual_segments": True,
            },
        },
        {
            "key": "video_8",
            "container": "fmp4",
            "elementary_streams": ["video_stream7"],
            "segment_settings": {
                "segment_duration": "4.0s",
                "individual_segments": True,
            },
        },
        {
            "key": "video_9",
            "container": "fmp4",
            "elementary_streams": ["video_stream8"],
            "segment_settings": {
                "segment_duration": "4.0s",
                "individual_segments": True,
            },
        },
        {
            "key": "video_10",
            "container": "fmp4",
            "elementary_streams": ["video_stream9"],
            "segment_settings": {
                "segment_duration": "4.0s",
                "individual_segments": True,
            },
        },
        {
            "key": "audio_1",
            "container": "fmp4",
            "elementary_streams": ["audio_stream0"],
            "segment_settings": {
                "segment_duration": "4.0s",
                "individual_segments": True,
            },
        },
        {
            "key": "audio_2",
            "container": "fmp4",
            "elementary_streams": ["audio_stream1"],
            "segment_settings": {
                "segment_duration": "4.0s",
                "individual_segments": True,
            },
        },
        {
            "key": "audio_3",
            "container": "fmp4",
            "elementary_streams": ["audio_stream2"],
            "segment_settings": {
                "segment_duration": "4.0s",
                "individual_segments": True,
            },
        },
        {
            "key": "audio_4",
            "container": "fmp4",
            "elementary_streams": ["audio_stream3"],
            "segment_settings": {
                "segment_duration": "4.0s",
                "individual_segments": True,
            },
        },
        {
            "key": "audio_5",
            "container": "fmp4",
            "elementary_streams": ["audio_stream4"],
            "segment_settings": {
                "segment_duration": "4.0s",
                "individual_segments": True,
            },
        },
        {
            "key": "audio_6",
            "container": "fmp4",
            "elementary_streams": ["audio_stream5"],
            "segment_settings": {
                "segment_duration": "4.0s",
                "individual_segments": True,
            },
        },
    ],
    "sprite_sheets": [
        # Generate a 10x10 sprite sheet with 108px width images for thumbnails
        transcoder_v1.types.SpriteSheet(
            file_prefix="thumbnail",
            sprite_width_pixels=500,
            column_count=1,
            row_count=1,
            total_count=1,
        ),
        transcoder_v1.types.SpriteSheet(
            file_prefix="small-sprite-sheet",
            sprite_width_pixels=108,
            column_count=10,
            row_count=10,
            total_count=100,
        ),
        # Generate a 10x10 sprite sheet with 216px width images for thumbnails
        transcoder_v1.types.SpriteSheet(
            file_prefix="large-sprite-sheet",
            sprite_width_pixels=216,
            column_count=10,
            row_count=10,
            total_count=100,
        ),
    ],
    "manifests": [
        {
            "file_name": "manifest.m3u8",
            "type_": "HLS",
            "mux_streams": ["10", "9", "8", "7", "6", "5", "4", "3"],
            # "mux_streams": ["10", "9", "8", "7", "6", "5", "4", "3", "2", "1"],
        },
        {
            "file_name": "manifest.mpd",
            "type_": "DASH",
            "mux_streams": [
                "video_10",
                "video_9",
                "video_8",
                "video_7",
                "video_6",
                "video_5",
                "video_4",
                "video_3",
                # "video_2",
                # "video_1",
                "audio_6",
                "audio_5",
                "audio_4",
                "audio_3",
                # "audio_2",
                # "audio_1",
            ],
        },
    ],
}

transcoder_config_to_mp4 = {
    "elementary_streams": [
        {
            "key": "video_stream0",
            "video_stream": {
                "h264": {
                    "bitrate_bps": 5000000,
                    "frame_rate": 30,
                    "gop_duration": "4.0s",
                    "pixel_format": "yuv420p",
                    "rate_control_mode": "vbr",
                    "crf_level": 10,
                    "b_frame_count": 3,
                    "profile": "main",
                    "enable_two_pass": True,
                    "preset": "medium",
                }
            },
        },
        {
            "key": "audio_stream0",
            "audio_stream": {"codec": "aac-he", "bitrate_bps": 96000},
        },
    ],
    "mux_streams": [
        {
            "key": "mux_stream0",
            "container": "mp4",
            "elementary_streams": ["video_stream0", "audio_stream0"],
        }
    ],
}


def create_transcode_job(
    input_uri: str, output_uri: str, topic_path: Optional[str] = None
) -> transcoder_v1.types.resources.Job:

    return transcode_job(transcoder_config_default, input_uri, output_uri, topic_path)


def create_to_mp4_transcode_job(
    input_uri: str,
    output_uri: str,
    output_file_name: str,
    topic_path: Optional[str] = None,
) -> transcoder_v1.types.resources.Job:
    copied_transcoder_config_to_mp4 = copy.deepcopy(transcoder_config_to_mp4)
    copied_transcoder_config_to_mp4["mux_streams"][0]["file_name"] = output_file_name
    logger.info(
        f"with output output file name {output_file_name} "
        f"and inpyt url {input_uri} and with output url {output_uri}"
    )
    return transcode_job(
        copied_transcoder_config_to_mp4, input_uri, output_uri, topic_path
    )


def create_transcode_jobs(input_output_uris: List[Tuple[str, str]]) -> List[str]:
    job_names = []
    for intput, output in input_output_uris:
        job = create_transcode_job(intput, output)
        job_names.append(job.name)
    return job_names


def transcode_job(
    config: dict, input_uri: str, output_uri: str, topic_path: Optional[str] = None
) -> transcoder_v1.types.resources.Job:
    parent = f"projects/{settings.gcp_project_id}/locations/{settings.transcode_job_location}"

    job = transcoder_v1.types.Job()
    job.input_uri = input_uri
    job.output_uri = output_uri

    config_default = transcoder_v1.types.JobConfig(**config)

    if topic_path:
        config_default.pubsub_destination = transcoder_v1.types.PubsubDestination(
            topic=topic_path
        )

    job.config = config_default

    response = client.create_job(parent=parent, job=job)
    logging.info(f"Job: {response.name} has been scheduled")
    return response


def create_transcode_job_template(
    project_id: str,
    location: str,
    template_id: str,
    job_config: transcoder_v1.types.JobConfig,
) -> transcoder_v1.types.resources.JobTemplate:
    parent = f"projects/{project_id}/locations/{location}"

    job_template = transcoder_v1.types.JobTemplate()
    job_template.name = (
        f"projects/{project_id}/locations/{location}/jobTemplates/{template_id}"
    )
    job_template.config = job_config

    response = client.create_job_template(
        parent=parent, job_template=job_template, job_template_id=template_id
    )
    return response


def get_job(
    project_id: str,
    location: str,
    job_id: str,
) -> transcoder_v1.types.resources.Job:
    name = f"projects/{project_id}/locations/{location}/jobs/{job_id}"
    response = client.get_job(name=name)
    return response


def get_job_with_name(job_full_name: str) -> transcoder_v1.types.resources.Job:
    response = client.get_job(name=job_full_name)
    return response


def list_jobs(
    project_id: str,
    location: str,
):
    parent = f"projects/{project_id}/locations/{location}"
    response = client.list_jobs(parent=parent)

    return [item.name for item in response.jobs]


async def wait_for_job_completion(job_name: str, poll_interval=3):
    while True:
        logger.info(f"with job name {job_name}")
        job = client.get_job(name=job_name)
        state = job.state
        if state == transcoder_v1.types.Job.ProcessingState.SUCCEEDED:
            logger.info(f"to mp4 job has been completed")
            break
        elif state == transcoder_v1.types.Job.ProcessingState.FAILED:
            logger.error(f"to mp4 job failed with error {job.error}")
            break
        else:
            logger.info(
                f"Job is in state: {state.name}. Waiting for {poll_interval} seconds before polling again..."
            )
            await asyncio.sleep(poll_interval)
