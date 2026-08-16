import sys
from faster_whisper import WhisperModel

def transcribe_file(model, audio_path):
    print(f"\n==========================================")
    print(f"TRANSCRIBING: {audio_path}")
    print(f"==========================================")
    segments, info = model.transcribe(audio_path, beam_size=5)
    full_text = []
    for segment in segments:
        text = f"[{segment.start:.2f}s -> {segment.end:.2f}s] {segment.text}"
        print(text)
        full_text.append(segment.text.strip())
    return " ".join(full_text)

def main():
    files = sys.argv[1:] if len(sys.argv) > 1 else ["1.ogg", "2.ogg", "3.ogg", "api.ogg"]
    model = WhisperModel("small", device="cpu", compute_type="int8")
    
    results = {}
    for f in files:
        try:
            results[f] = transcribe_file(model, f)
        except Exception as e:
            print(f"Error transcribing {f}: {e}")
            results[f] = f"Error: {e}"
            
    with open("client_responses.txt", "w", encoding="utf-8") as out:
        for f, text in results.items():
            out.write(f"=== {f} ===\n{text}\n\n")
            
    print("\n--- ALL DONE ---")

if __name__ == "__main__":
    main()
