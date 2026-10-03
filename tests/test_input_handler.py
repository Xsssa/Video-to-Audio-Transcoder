import sys
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ui.input_handler import (
    SUPPORTED_VIDEO_EXTENSIONS,
    is_supported_video,
    parse_tokens,
    strip_quotes,
    get_clipboard_files,
    expand_path,
)

def test_input_handler():
    assert len(SUPPORTED_VIDEO_EXTENSIONS) >= 14
    assert ".mp4" in SUPPORTED_VIDEO_EXTENSIONS
    assert ".mkv" in SUPPORTED_VIDEO_EXTENSIONS
    assert ".webm" in SUPPORTED_VIDEO_EXTENSIONS

    # Test strip quotes
    assert strip_quotes('"F:\\test.mp4"') == "F:\\test.mp4"
    assert strip_quotes("'F:\\test.mp4'") == "F:\\test.mp4"
    assert strip_quotes("  'F:\\test.mp4'  ") == "F:\\test.mp4"

    # Test parse tokens
    raw1 = '"F:\\vid1.mp4" "F:\\vid2.mkv" F:\\simple.webm'
    tokens1 = parse_tokens(raw1)
    assert len(tokens1) == 3
    assert tokens1[0] == "F:\\vid1.mp4"
    assert tokens1[1] == "F:\\vid2.mkv"
    assert tokens1[2] == "F:\\simple.webm"

    # Test single quotes
    raw2 = "'F:\\My Videos\\vid 1.mp4' 'F:\\My Videos\\vid 2.mkv'"
    tokens2 = parse_tokens(raw2)
    assert len(tokens2) == 2
    assert tokens2[0] == "F:\\My Videos\\vid 1.mp4"
    assert tokens2[1] == "F:\\My Videos\\vid 2.mkv"

    # Test powershell & operator
    raw3 = '& "F:\\test.mp4"'
    tokens3 = parse_tokens(raw3)
    assert len(tokens3) == 1
    assert tokens3[0] == "F:\\test.mp4"

    # Test multiline
    raw4 = "F:\\test1.mp4\nF:\\test2.mkv"
    tokens4 = parse_tokens(raw4)
    assert len(tokens4) == 2

    print("All input handler tests passed successfully!")

if __name__ == "__main__":
    test_input_handler()
