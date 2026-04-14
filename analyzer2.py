from pydantic import BaseModel, Field
from google import genai
from google.genai import types
from google.genai import errors
import shelve
import sys
import os
import os.path
import time

PROMPT="""You are a precise archival researcher. Analyze the provided PDF from the archives of the Massachusetts Institute of Technology and determine the probability (0-10) that it contains significant
        connections between MIT and the state of Louisiana or a city therein."""

# 2. Initialize Client
client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

def get_cached_uri(client, local_path, shelf_name="google_cache.shelf"):
    """
    Returns a valid URI for the given local_path.
    Checks a local shelf; if missing or expired (>47h), re-uploads.
    """
    abs_path = os.path.abspath(local_path)

    with shelve.open(shelf_name, writeback=True) as shelf:
        # Check if we have a record and if it's still fresh (Gemini limit is 48h)
        if abs_path in shelf:
            entry = shelf[abs_path]
            # Expire 1 hour early to be safe
            if time.time() - entry['timestamp'] < (47 * 3600):
                try:
                    # Probe server to ensure it wasn't manually deleted
                    client.files.get(name=entry['name'])
                    return entry['uri']
                except errors.ClientError:
                    print(f"Cache hit but file missing on server. Re-uploading...")
            else:
                print(f"Cache expired for {abs_path}. Re-uploading...")

        # If we reach here, we need to upload
        print(f"Uploading {abs_path}...")
        uploaded_file = client.files.upload(path=abs_path)

        # Store metadata in shelf
        shelf[abs_path] = {
            'uri': uploaded_file.uri,
            'name': uploaded_file.name,
            'timestamp': time.time()
        }

        return uploaded_file.uri

def list_files_on_server():
    print("Currently active files on server:")
    for f in client.files.list():
        print(f"- {f.display_name} ({f.name}) | Expires: {f.expiration_time}")

list_files_on_server()


# 1. Define your "Slots"
class LouisianaConnection(BaseModel):
    who: str = Field(description="Name of the person, family or Louisana company")
    what: str = Field(description="The nature of the connection to MIT")
    time_period: str = Field(description="The relevant years or era")
    page_numbers: list[int] = Field(
        description="A list of 1-based page numbers in the PDF where this information appears"
    )

class AnalysisResult(BaseModel):
    path: str | None = None
    processing_time: float | None = None
    score: int = Field(ge=0, le=10, description="Probability score")
    connections: list[LouisianaConnection]


# NEW: Get the filename from the command line
if len(sys.argv) < 2:
    print("Usage: python analyzer2.py <path_to_pdf>")
    sys.exit(1)

uri = get_cached_urk(client, pdf_path)
print("uri=",uri)

# 3. Fill the Slots
t0 = time.time()
response = client.models.generate_content(
    model="gemini-3-flash-preview",
    contents=[PROMPT,
              types.Part.from_uri(
                  file_uri=uri,
                  mime_type="application/pdf",
                  media_resolution={"level": "media_resolution_high"} # <--- Specified here
              )],
    config=types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=AnalysisResult,  # Directly pass the class
    ),
)
t1 = time.time()

# 4. Access parsed data (already a Pydantic object)
data = response.parsed
print(f"Filename: {os.path.basename(pdf_path)} Score: {data.score}")
for conn in data.connections:
    print(f"- {conn.who}: {conn.what} ({conn.time_period}) ({conn.page_numbers})")

usage = response.usage_metadata
print(f"Prompt Tokens: {usage.prompt_token_count}")
print(f"Candidates Tokens: {usage.candidates_token_count}")
print(f"Total Tokens: {usage.total_token_count}")
print(f"Total Time: {t1-t0}")

with open("cost.csv","a") as f:
    f.write(f"{usage.prompt_token_count},{usage.candidates_token_count},{usage.total_token_count}\n")

with open("output.jsons","a") as f:
    data = response.parsed
    data.path = pdf_path
    data.processing_time = t1-t0
    print(data.model_dump_json(indent=2),file=f)
