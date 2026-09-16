"""Optional faster-whisper adapter: installed package and predownloaded local model."""
import argparse
import os
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser(description='Transcribe an ACC recording locally')
    parser.add_argument('--audio', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--model-dir', required=True)
    parser.add_argument('--device', default='cpu', choices=('cpu', 'cuda'))
    parser.add_argument('--compute-type', default='int8')
    args = parser.parse_args()
    model_dir = Path(args.model_dir).resolve()
    # Never interpret a missing local path as an online model identifier.
    if not all((model_dir / name).is_file() for name in ('model.bin', 'config.json', 'tokenizer.json')):
        parser.error('Use a downloaded faster-whisper model directory containing model.bin, config.json, and tokenizer.json.')
    os.environ['HF_HUB_OFFLINE'] = '1'
    try:
        from faster_whisper import WhisperModel
        model = WhisperModel(str(model_dir), device=args.device, compute_type=args.compute_type)
        segments, _ = model.transcribe(args.audio, beam_size=5)
        text = ' '.join(segment.text.strip() for segment in segments).strip()
        if not text:
            raise ValueError('No speech was recognized; the recording remains saved.')
        output = Path(args.output)
        temporary = output.with_suffix('.tmp')
        temporary.write_text(text, encoding='utf-8')
        temporary.replace(output)
        print('Local transcription complete.', flush=True)
        return 0
    except (ImportError, ValueError, RuntimeError, OSError) as exc:
        print('Local transcription: ' + str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
