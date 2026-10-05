# ruff: noqa
# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import base64
import datetime
import json
import os
import uuid
from zoneinfo import ZoneInfo

import requests
from a2ui.basic_catalog.provider import BasicCatalog
from a2ui.schema.manager import A2uiSchemaManager
from google import genai
from google.adk.agents import Agent
from google.adk.agents.callback_context import CallbackContext
from google.adk.apps import App
from google.adk.code_executors import AgentEngineSandboxCodeExecutor
from google.adk.memory import VertexAiMemoryBankService
from google.adk.models import Gemini
from google.adk.tools import ToolContext
from google.adk.tools.load_memory_tool import LoadMemoryTool
from google.adk.tools.preload_memory_tool import PreloadMemoryTool
from google.cloud import firestore, storage
from google.genai import types

try:
    from app.a2ui_utils import a2ui_callback
except ImportError:
    from a2ui_utils import a2ui_callback

# Hardcoded project ID as string for Firestore client
PROJECT_ID = "qwiklabs-gcp-03-5d4f25a91be7"
# Hardcoded bucket name as string for Cloud Storage
BUCKET_NAME = "fit-coach-ai-assets-qwiklabs-gcp-03-5d4f25a91be7"
# Memory Bank ID (reused Agent Engine ID)
MEMORY_BANK_ID = "2255090651987181568"

db = firestore.Client(project=PROJECT_ID)
storage_client = storage.Client(project=PROJECT_ID)

# Load Agent Engine resource name from deployment_metadata.json if available
agent_engine_resource_name = None
metadata_path = os.path.join(os.path.dirname(__file__), "..", "deployment_metadata.json")
if os.path.exists(metadata_path):
    try:
        with open(metadata_path, "r") as f:
            metadata = json.load(f)
            agent_engine_resource_name = metadata.get("remote_agent_runtime_id")
    except Exception:
        pass

code_executor = AgentEngineSandboxCodeExecutor(
    agent_engine_resource_name=agent_engine_resource_name
)


def memory_service_builder():
    """Builds the Vertex AI Memory Bank service for deployment."""
    return VertexAiMemoryBankService(
        project=PROJECT_ID,
        location="us-east1",
        agent_engine_id=MEMORY_BANK_ID,
    )


async def generate_memories_callback(callback_context: CallbackContext):
    """WRITE: After each turn, send session events to Memory Bank for extraction."""
    await callback_context.add_session_to_memory()
    return None


schema_manager = A2uiSchemaManager(
    version="0.8",
    catalogs=[BasicCatalog.get_config("0.8")],
)

instruction = schema_manager.generate_system_prompt(
    role_description=(
        "You are a helpful AI fitness coach assistant. You remember all user activities, "
        "workout logs, fitness goals, exercise performance, and stated preferences from "
        "previous conversations, and use them to personalize your responses. You can look up "
        "exercises, calculate 1RM targets, convert addresses to coordinates with Geocoding, "
        "find nearby gym/fitness places using Google Places API, generate exercise illustrations, "
        "generate exercise videos with Google Omni model, search cross-session memory, and execute Python code in a safe sandbox."
    ),
    workflow_description="Analyze the request and return structured UI when appropriate.",
    ui_description=(
        "Keep every surface tiny and flat: ONE Card > ONE Column > a few Text rows. "
        "Never nest a Card inside a Card. "
        "Use ONLY these components: Card, Column, Row, Text, and Image. Do not use "
        "Table or Heading (unsupported), or Buttons, actions, or forms (they do "
        "nothing in adk web). "
        "You may include one Image component, but only when you have a public https "
        "URL for the image (for example the URL an image tool returns after uploading "
        "to a public bucket). Set the Image url to that exact https link, for example "
        "{\"Image\": {\"url\": {\"literalString\": \"https://...\"}}}. Never point an "
        "Image at a bare filename, an artifact name, or a non-http(s) path. If you do "
        "not have a public URL, add a short Text line noting the image instead. "
        "No markdown in text; use the usageHint property ('h1', 'h2', 'body') for "
        "headings and emphasis. "
        "Output ONLY the raw A2UI JSON array — no prose, and never wrap it in "
        "<a2a_datapart_json> tags or 'kind'/'data'/'metadata' objects."
    ),
    include_schema=True,
    include_examples=True,
)


def get_weather(query: str) -> str:
    """Simulates a web search. Use it get information on weather.

    Args:
        query: A string containing the location to get weather information for.

    Returns:
        A string with the simulated weather information for the queried location.
    """
    if "sf" in query.lower() or "san francisco" in query.lower():
        return "It's 60 degrees and foggy."
    return "It's 90 degrees and sunny."


def get_current_time(query: str) -> str:
    """Simulates getting the current time for a city.

    Args:
        city: The name of the city to get the current time for.

    Returns:
        A string with the current time information.
    """
    if "sf" in query.lower() or "san francisco" in query.lower():
        tz_identifier = "America/Los_Angeles"
    else:
        return f"Sorry, I don't have timezone information for query: {query}."

    tz = ZoneInfo(tz_identifier)
    now = datetime.datetime.now(tz)
    return f"The current time for query {query} is {now.strftime('%Y-%m-%d %H:%M:%S %Z%z')}"


def search_exercises(category: str = "", equipment: str = "") -> str:
    """Search for exercises in the Firestore database by muscle category or equipment.

    Args:
        category: Filter exercises by muscle category (e.g. Chest, Legs, Back, Shoulders).
        equipment: Filter exercises by equipment (e.g. Barbell, Dumbbell, Kettlebell, Bodyweight).

    Returns:
        A string listing matching exercises or a status message.
    """
    try:
        query = db.collection("exercises")
        if category:
            query = query.where("category", "==", category)
        if equipment:
            query = query.where("equipment", "==", equipment)
        docs = list(query.stream())
        if not docs:
            return "No exercises found matching the criteria."
        results = [doc.to_dict() for doc in docs]
        return f"Found {len(results)} exercises: {results}"
    except Exception as e:
        return f"Error reading exercises from Firestore: {e}"


def add_exercise(name: str, category: str, equipment: str, difficulty: str, description: str) -> str:
    """Add a new exercise item to the Firestore exercises collection.

    Args:
        name: Name of the exercise (e.g., Deadlift).
        category: Muscle group category (e.g., Back, Legs, Chest, Shoulders).
        equipment: Equipment required (e.g., Barbell, Dumbbell, Kettlebell, Bodyweight).
        difficulty: Difficulty level (e.g., Beginner, Intermediate, Advanced).
        description: Brief execution instructions or form tips.

    Returns:
        Confirmation string indicating the exercise was added.
    """
    try:
        doc_ref = db.collection("exercises").document()
        exercise_data = {
            "id": doc_ref.id,
            "name": name,
            "category": category,
            "equipment": equipment,
            "difficulty": difficulty,
            "description": description,
        }
        doc_ref.set(exercise_data)
        return f"Successfully added exercise '{name}' with ID '{doc_ref.id}' to Firestore."
    except Exception as e:
        return f"Error adding exercise to Firestore: {e}"


def calculate_one_rep_max(weight: float, reps: int) -> str:
    """Calculate estimated one-rep max (1RM) and training targets using Epley formula.

    Args:
        weight: The weight lifted (in lbs or kg).
        reps: Number of repetitions performed.

    Returns:
        A string with estimated 1RM and target percentages for training sets.
    """
    if reps <= 0 or weight <= 0:
        return "Weight and reps must be positive numbers."
    if reps == 1:
        one_rm = weight
    else:
        one_rm = weight * (1 + reps / 30.0)

    one_rm = round(one_rm, 1)
    p90 = round(one_rm * 0.90, 1)
    p80 = round(one_rm * 0.80, 1)
    p70 = round(one_rm * 0.70, 1)

    return (
        f"Estimated 1RM: {one_rm}. "
        f"Training targets -> 90%: {p90}, 80%: {p80}, 70%: {p70}."
    )


def get_public_exercise_info(limit: int = 3) -> str:
    """Fetch public exercise information from the Wger open-source fitness API.

    Args:
        limit: Number of exercise items to fetch (default: 3).

    Returns:
        A formatted string listing exercise names, categories, and equipment.
    """
    api_url = os.getenv("WGER_API_URL", "https://wger.de/api/v2/exerciseinfo/")
    api_key = os.getenv("WGER_API_KEY", "")

    headers = {}
    if api_key:
        headers["Authorization"] = f"Token {api_key}"

    try:
        response = requests.get(api_url, params={"limit": limit}, headers=headers, timeout=10)
        if response.status_code != 200:
            return f"API request failed with status code {response.status_code}"

        data = response.json()
        results = data.get("results", [])
        if not results:
            return "No exercise data returned from API."

        summary = []
        for item in results:
            category = item.get("category", {}).get("name", "General")
            translations = item.get("translations", [])
            name = "Exercise"
            for t in translations:
                if t.get("language") == 2 and t.get("name"):  # English
                    name = t.get("name")
                    break
            if name == "Exercise" and translations:
                name = translations[0].get("name", "Exercise")

            equip_list = [eq.get("name") for eq in item.get("equipment", []) if eq.get("name")]
            equip_str = ", ".join(equip_list) if equip_list else "None required"
            summary.append(f"- {name} (Category: {category}, Equipment: {equip_str})")

        return "Public Exercise API Data:\n" + "\n".join(summary)
    except Exception as e:
        return f"Error fetching public exercise data: {e}"


def geocode_address(address: str) -> str:
    """Convert an address or location name into geographic coordinates (latitude and longitude) using Google Geocoding API.

    Args:
        address: The address or place name to geocode (e.g. '1600 Amphitheatre Pkwy, Mountain View, CA').

    Returns:
        A formatted string with formatted address and location coordinates (lat, lng).
    """
    api_key = os.getenv("GOOGLE_MAPS_API_KEY", "")
    if not api_key or api_key == "PASTE_KEY_HERE":
        return "Error: GOOGLE_MAPS_API_KEY is not set or valid in environment."

    url = "https://maps.googleapis.com/maps/api/geocode/json"
    params = {"address": address, "key": api_key}

    try:
        response = requests.get(url, params=params, timeout=10)
        data = response.json()
        if data.get("status") != "OK" or not data.get("results"):
            return f"Geocoding failed: {data.get('status', 'No results found')}"

        result = data["results"][0]
        formatted_address = result.get("formatted_address")
        location = result.get("geometry", {}).get("location", {})
        lat = location.get("lat")
        lng = location.get("lng")

        return f"Address: {formatted_address} | Location: (lat: {lat}, lng: {lng})"
    except Exception as e:
        return f"Error executing Geocoding API request: {e}"


def search_nearby_places(latitude: float, longitude: float, place_type: str = "gym", radius_meters: float = 5000.0) -> str:
    """Search for nearby places of a given type around a geographic location using Google Places API (New).

    Args:
        latitude: Latitude coordinate of the search center.
        longitude: Longitude coordinate of the search center.
        place_type: Type of place to search for (e.g., 'gym', 'fitness_center', 'park', 'sports_complex').
        radius_meters: Search radius in meters (default 5000.0).

    Returns:
        A string listing nearby places with key fields (name, address, location).
    """
    api_key = os.getenv("GOOGLE_MAPS_API_KEY", "")
    if not api_key or api_key == "PASTE_KEY_HERE":
        return "Error: GOOGLE_MAPS_API_KEY is not set or valid in environment."

    url = "https://places.googleapis.com/v1/places:searchNearby"
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": "places.displayName,places.formattedAddress,places.location",
    }
    payload = {
        "includedTypes": [place_type],
        "locationRestriction": {
            "circle": {
                "center": {
                    "latitude": latitude,
                    "longitude": longitude,
                },
                "radius": radius_meters,
            }
        },
    }

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=10)
        if response.status_code != 200:
            return f"Places API request failed with status code {response.status_code}: {response.text}"

        data = response.json()
        places = data.get("places", [])
        if not places:
            return f"No nearby '{place_type}' places found within {radius_meters}m."

        results = []
        for p in places:
            name = p.get("displayName", {}).get("text", "Unknown Place")
            addr = p.get("formattedAddress", "N/A")
            loc = p.get("location", {})
            results.append(f"- Name: {name} | Address: {addr} | Location: (lat: {loc.get('latitude')}, lng: {loc.get('longitude')})")

        return f"Nearby Places ({place_type}):\n" + "\n".join(results)
    except Exception as e:
        return f"Error executing Places API request: {e}"


async def generate_exercise_image(prompt: str, tool_context: ToolContext) -> str:
    """Generate an image for a fitness item using gemini-3.1-flash-lite-image in the global region.

    Saves the image bytes as a session artifact and uploads it to public Cloud Storage.

    Args:
        prompt: Detailed description of the fitness image to generate (e.g. 'A dumbbell bicep curl exercise technique guide').
        tool_context: ADK tool context for saving artifacts.

    Returns:
        The public HTTPS URL of the uploaded image in Cloud Storage.
    """
    try:
        genai_client = genai.Client(vertexai=True, project=PROJECT_ID, location="global")
        response = genai_client.models.generate_content(
            model="gemini-3.1-flash-lite-image",
            contents=f"High quality fitness illustration: {prompt}",
            config=types.GenerateContentConfig(response_modalities=["IMAGE"]),
        )

        image_bytes = None
        mime_type = "image/jpeg"
        if response.candidates and response.candidates[0].content.parts:
            for part in response.candidates[0].content.parts:
                if part.inline_data:
                    image_bytes = part.inline_data.data
                    if part.inline_data.mime_type:
                        mime_type = part.inline_data.mime_type
                    break

        if not image_bytes:
            return "Failed to generate image bytes from model response."

        filename = f"exercise_{uuid.uuid4().hex[:8]}.jpg"

        # 1. Save with tool_context.save_artifact so it shows up in Playground's Artifacts panel
        artifact_part = types.Part.from_bytes(data=image_bytes, mime_type=mime_type)
        await tool_context.save_artifact(filename=filename, artifact=artifact_part)

        # 2. Upload same bytes directly to public Cloud Storage bucket
        bucket = storage_client.bucket(BUCKET_NAME)
        blob = bucket.blob(filename)
        blob.upload_from_string(image_bytes, content_type=mime_type)

        public_url = f"https://storage.googleapis.com/{BUCKET_NAME}/{filename}"
        return f"Image generated successfully. Public URL: {public_url}"

    except Exception as e:
        return f"Error generating exercise image: {e}"


async def generate_exercise_video(prompt: str, tool_context: ToolContext) -> str:
    """Generate a short fitness video using Google Omni model (gemini-omni-flash-preview) in the global region.

    Saves the video bytes as a session artifact and uploads it to public Cloud Storage.

    Args:
        prompt: Detailed description of the fitness video to generate (e.g. 'A short animation of a person performing a squat with proper form').
        tool_context: ADK tool context for saving artifacts.

    Returns:
        The public HTTPS URL of the uploaded video in Cloud Storage.
    """
    try:
        genai_client = genai.Client(vertexai=True, project=PROJECT_ID, location="global")
        interaction = genai_client.interactions.create(
            model="gemini-omni-flash-preview",
            input=f"Generate a short video animation: {prompt}",
            generation_config={"response_modalities": ["VIDEO"]},
        )

        video_bytes = None
        mime_type = "video/mp4"

        if interaction.output_video and getattr(interaction.output_video, "data", None):
            raw_data = interaction.output_video.data
            if isinstance(raw_data, str):
                video_bytes = base64.b64decode(raw_data)
            else:
                video_bytes = raw_data
            if getattr(interaction.output_video, "mime_type", None):
                mime_type = interaction.output_video.mime_type
        elif getattr(interaction, "outputs", None):
            for output in interaction.outputs:
                for part in getattr(output, "parts", []):
                    if getattr(part, "inline_data", None) and getattr(part.inline_data, "data", None):
                        raw_data = part.inline_data.data
                        if isinstance(raw_data, str):
                            video_bytes = base64.b64decode(raw_data)
                        else:
                            video_bytes = raw_data
                        if getattr(part.inline_data, "mime_type", None):
                            mime_type = part.inline_data.mime_type
                        break

        if not video_bytes:
            return "Failed to generate video bytes from model response."

        filename = f"exercise_video_{uuid.uuid4().hex[:8]}.mp4"

        # 1. Save with tool_context.save_artifact so it shows up in Playground's Artifacts panel
        artifact_part = types.Part.from_bytes(data=video_bytes, mime_type=mime_type)
        await tool_context.save_artifact(filename=filename, artifact=artifact_part)

        # 2. Upload same bytes directly to public Cloud Storage bucket
        bucket = storage_client.bucket(BUCKET_NAME)
        blob = bucket.blob(filename)
        blob.upload_from_string(video_bytes, content_type=mime_type)

        public_url = f"https://storage.googleapis.com/{BUCKET_NAME}/{filename}"
        return f"Video generated successfully. Public URL: {public_url}"

    except Exception as e:
        return f"Error generating exercise video: {e}"


root_agent = Agent(
    name="root_agent",
    model=Gemini(
        model="gemini-2.5-flash",
        retry_options=types.HttpRetryOptions(attempts=3),
    ),
    instruction=instruction,
    code_executor=code_executor,
    tools=[
        PreloadMemoryTool(),
        LoadMemoryTool(),
        get_weather,
        get_current_time,
        search_exercises,
        add_exercise,
        calculate_one_rep_max,
        get_public_exercise_info,
        geocode_address,
        search_nearby_places,
        generate_exercise_image,
        generate_exercise_video,
    ],
    after_agent_callback=generate_memories_callback,
    after_model_callback=a2ui_callback,
)

app = App(
    root_agent=root_agent,
    name="app",
)
