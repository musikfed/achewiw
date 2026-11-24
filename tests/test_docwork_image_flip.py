"""DOCWORK native mirror tests; no server, GPU, Windows app or real manuscript."""
import io
import unittest
from hashlib import sha256

from docx import Document
from docx.oxml.ns import qn
from PIL import Image

from docwork_image_layout import apply_image_layout, IllustrationLayoutError


def test_document():
    source = Image.new("RGB", (10, 8), "white")
    pix = source.load()
    pix[1, 1] = (255, 0, 0)
    pix[8, 1] = (0, 255, 0)
    pix[1, 6] = (0, 0, 255)
    pix[8, 6] = (255, 255, 0)
    image = io.BytesIO()
    source.save(image, format="PNG")
    doc = Document()
    doc.add_paragraph().add_run().add_picture(io.BytesIO(image.getvalue()))
    doc.add_paragraph().add_run().add_picture(io.BytesIO(image.getvalue()))
    raw = io.BytesIO()
    doc.save(raw)
    return raw.getvalue()


def picture(doc, index):
    blip = doc.inline_shapes[index]._inline.xpath(".//a:blip")[0]
    relation = blip.get(qn("r:embed"))
    with Image.open(io.BytesIO(doc.part.related_parts[relation].blob)) as image:
        return image.convert("RGB").copy()


class MirrorImageTests(unittest.TestCase):
    def test_horizontal_and_vertical_and_both(self):
        initial = test_document()
        digest = sha256(initial).hexdigest()
        corners = [(1, 1), (8, 1), (1, 6), (8, 6)]
        for horizontal, vertical in [(True, False), (False, True), (True, True)]:
            with self.subTest(horizontal=horizontal, vertical=vertical):
                doc = Document(io.BytesIO(initial))
                apply_image_layout(doc, {"0": {
                    "width_percent": 70, "align": "center",
                    "corner_percent": 0,
                    "flip_horizontal": horizontal, "flip_vertical": vertical
                }})
                left = picture(doc, 0)
                untouched = picture(doc, 1)
                for x, y in corners:
                    source_x = 9 - x if horizontal else x
                    source_y = 7 - y if vertical else y
                    self.assertEqual(left.getpixel((x, y)),
                                     untouched.getpixel((source_x, source_y)))
                self.assertEqual(untouched.getpixel((1, 1)), (255, 0, 0))
                self.assertEqual(sha256(initial).hexdigest(), digest)

    def test_flags_require_true_boolean(self):
        for invalid in (1, "true", None, [], 0):
            with self.subTest(invalid=invalid):
                doc = Document(io.BytesIO(test_document()))
                with self.assertRaises(IllustrationLayoutError):
                    apply_image_layout(doc, {"0": {"flip_horizontal": invalid}})

    def test_rounding_and_flip_are_combined(self):
        doc = Document(io.BytesIO(test_document()))
        apply_image_layout(doc, {"0": {
            "flip_horizontal": True, "flip_vertical": False,
            "corner_percent": 8, "width_percent": 70, "align": "center"
        }})
        self.assertEqual(picture(doc, 0).getpixel((1, 1)), (0, 255, 0))
        self.assertEqual(picture(doc, 1).getpixel((1, 1)), (255, 0, 0))


if __name__ == "__main__":
    unittest.main()
