import numpy as np

from kippiesearch.embeddings import build_onnx_feed, dummy_feature_array


class _Inp:
    def __init__(self, name: str, shape, type_: str) -> None:
        self.name = name
        self.shape = shape
        self.type = type_


def test_dummy_features_are_zero_rows():
    inp = _Inp("image_features", ["num_image_tokens", 512], "tensor(float)")
    arr = dummy_feature_array(inp)
    assert arr.shape == (0, 512)
    assert arr.dtype == np.float32


def test_feed_includes_empty_multimodal_inputs():
    inputs = [
        _Inp("input_ids", ["batch", "seq"], "tensor(int64)"),
        _Inp("attention_mask", ["batch", "seq"], "tensor(int64)"),
        _Inp("image_features", ["num_image_tokens", 512], "tensor(float)"),
        _Inp("video_features", ["num_video_tokens", 512], "tensor(float)"),
        _Inp("audio_features", ["num_audio_tokens", 512], "tensor(float)"),
    ]
    ids = np.ones((2, 8), dtype=np.int64)
    mask = np.ones((2, 8), dtype=np.int64)
    feed = build_onnx_feed(inputs, ids, mask)
    assert set(feed) == {
        "input_ids",
        "attention_mask",
        "image_features",
        "video_features",
        "audio_features",
    }
    assert feed["input_ids"].shape == (2, 8)
    for name in ("image_features", "video_features", "audio_features"):
        assert feed[name].shape[0] == 0
        assert feed[name].shape[-1] == 512
