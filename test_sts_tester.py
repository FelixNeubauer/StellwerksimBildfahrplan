import unittest

from sts_tester import LineXMLFramer


class LineXMLFramerTests(unittest.TestCase):
    def test_fragmented_message(self):
        framer = LineXMLFramer()
        self.assertEqual(framer.feed(b'<zugliste'), [])
        self.assertEqual(framer.feed(b' />\n'), [b'<zugliste />'])

    def test_multiple_messages_and_crlf(self):
        framer = LineXMLFramer()
        self.assertEqual(
            framer.feed(b'<status code="200" />\r\n<event zid="1" />\n'),
            [b'<status code="200" />', b'<event zid="1" />'],
        )

    def test_remainder_is_preserved(self):
        framer = LineXMLFramer()
        framer.feed(b'<partial')
        self.assertEqual(framer.remainder(), b'<partial')


if __name__ == "__main__":
    unittest.main()
