import sys
from faster_whisper import WhisperModel

def main():
    audio_path = sys.argv[1] if len(sys.argv) > 1 else "audio_2026-08-16_01-09-55.ogg"
    print(f"Transcribing {audio_path}...")
    
    # Use 'small' or 'base' model on CPU
    model_size = "small"
    model = WhisperModel(model_size, device="cpu", compute_type="int8")
    
    segments, info = model.transcribe(audio_path, beam_size=5)
    
    print(f"Detected language: {info.language} with probability {info.language_probability:.2f}")
    
    full_text = []
    for segment in segments:
        text = f"[{segment.start:.2f}s -> {segment.end:.2f}s] {segment.text}"
        print(text)
        full_text.append(segment.text.strip())
        
    result_text = "\n".join(full_text)
    with open("transcription.txt", "w", encoding="utf-8") as f:
        f.write(result_text)
        
    print("\n--- FULL TRANSCRIPTION ---")
    print(result_text)

if __name__ == "__main__":
    main()
