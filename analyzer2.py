from pydantic import BaseModel, Field
from google import genai
from google.genai import types
from google.genai import errors
from google.genai.types import GenerateContentResponseUsageMetadata
import shelve
import sys
import os
import os.path
import time

PROMPT="""You are a precise archival researcher. Analyze the provided PDF from the archives of the Massachusetts Institute of Technology and determine the probability (0-10) that it contains significant
        connections between MIT and the state of Louisiana or a city therein."""

PROMPT2 = "Analyze the attached report for Louisiana connections..."
MODEL = "gemini-3-flash-preview"

class LouisianaConnection(BaseModel):
    who: str = Field(description="Name of the person, family or Louisana company")
    what: str = Field(description="The nature of the connection to MIT")
    time_period: str = Field(description="The relevant years or era")
    page_numbers: list[int] = Field(
        description="A list of 1-based page numbers in the PDF where this information appears"
    )

class AnalysisResult(BaseModel):
    score: int = Field(description="Probability score from 0 to 10")
    connections: list[LouisianaConnection]

# 2. Application Schema (For local storage tracking)
class ArchiveEntry(BaseModel):
    filename: str
    timestamp: float
    processing_time: float
    data: AnalysisResult
    usage: GenerateContentResponseUsageMetadata
    model_version: str = MODEL


# 2. Initialize Client
client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

#client = genai.Client(
#    vertexai=True,
#    project=os.getenv("GOOGLE_CLOUD_PROJECT"),
#    location="us-central1"
#)

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
        uploaded_file = client.files.upload(file=abs_path)

        # Block until document processing is complete
        while uploaded_file.state.name == "PROCESSING":
            print("Processing PDF on server...", end="\r")
            time.sleep(2)
            uploaded_file = client.files.get(name=uploaded_file.name)
        print("\nProcessing complete.")

        if uploaded_file.state.name == "FAILED":
            raise RuntimeError(f"Server failed to process {abs_path}")

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


# NEW: Get the filename from the command line
if len(sys.argv) < 2:
    print("Usage: python analyzer2.py <path_to_pdf>")
    sys.exit(1)

pdf_path = sys.argv[1]
uri = get_cached_uri(client, pdf_path)
print("uri=",uri)

# 3. Fill the Slots
# media_resolution={"level": "media_resolution_high"} # <--- Specified here
t0 = time.time()
response = client.models.generate_content(
    model=MODEL,
    contents=[PROMPT,
              types.Part.from_uri(
                  file_uri=uri,
                  mime_type="application/pdf"
              )],
    config=types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=AnalysisResult,
#        temperature=0.0
    ),
)
t1 = time.time()

# To Archive:
entry = ArchiveEntry(
    filename=os.path.basename(pdf_path),
    timestamp=time.time(),
    processing_time=t1-t0,
    data=response.parsed,
    usage=response.usage_metadata
)

# 4. Access parsed data (already a Pydantic object)

print(f"Filename: {entry.filename} Score: {entry.data.score}")
for conn in entry.data.connections:
    print(f"- {conn.who}: {conn.what} ({conn.time_period}) ({conn.page_numbers})")

print(f"Prompt Tokens: {entry.usage.prompt_token_count}")
print(f"Candidates Tokens: {entry.usage.candidates_token_count}")
print(f"Total Tokens: {entry.usage.total_token_count}")
print(f"Total Time: {t1-t0}")

with open("cost.csv","a") as f:
    f.write(f"{entry.usage.prompt_token_count},{entry.usage.candidates_token_count},{entry.usage.total_token_count}\n")

with open("output.jsonl","a") as f:
    print(entry.model_dump_json(indent=2),file=f)
