from typing import Optional

_whisper_model = None

def get_whisper_model():
    """
    Returns a cached, singleton instance of the WhisperModel.
    """
    global _whisper_model
    if _whisper_model is None:
        from faster_whisper import WhisperModel
        # Instantiate the model. Using 'base', 'cpu', 'int8' as was hardcoded.
        _whisper_model = WhisperModel("base", device="cpu", compute_type="int8")
    return _whisper_model
