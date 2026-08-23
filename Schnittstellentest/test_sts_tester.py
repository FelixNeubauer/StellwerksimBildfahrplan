import unittest
from sts_tester import LineXMLFramer, StellwerkSimProtocolParser, StellwerkSimTesterGUI


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


class ProtocolParserTests(unittest.TestCase):
    ZUGLISTE = (
        b"<zugliste >\n"
        b"<zug zid='72230' name='RE 32924' />\n"
        b"<zug zid='126377' name='RE 32925' />\n"
        b"</zugliste>\n"
    )

    @staticmethod
    def feed_packets(*packets):
        framer = LineXMLFramer()
        parser = StellwerkSimProtocolParser()
        results = []
        for packet in packets:
            for line in framer.feed(packet):
                results.append(parser.feed_line(line))
        return parser, results

    def test_complete_multiline_train_list_extracts_in_order(self):
        parser, results = self.feed_packets(self.ZUGLISTE)
        self.assertEqual([result.state for result in results], ["pending", "pending", "pending", "complete"])
        self.assertEqual(
            [(train.name, train.zid) for train in parser.trains],
            [("RE 32924", "72230"), ("RE 32925", "126377")],
        )
        self.assertEqual(results[-1].element.tag, "zugliste")
        self.assertFalse(parser.in_container)
        self.assertTrue(all(result.error is None for result in results))
        self.assertEqual(results[-1].raw_document, self.ZUGLISTE.rstrip(b"\n"))

    def test_train_list_fragmented_across_recv_calls(self):
        parser, results = self.feed_packets(
            b"<zugli",
            b"ste >\n<zug zid='72230' na",
            b"me='RE 32924' />\n</zug",
            b"liste>\n",
        )
        self.assertEqual([result.state for result in results], ["pending", "pending", "complete"])
        self.assertEqual(len(parser.trains), 1)
        self.assertEqual((parser.trains[0].name, parser.trains[0].zid), ("RE 32924", "72230"))

    def test_previous_train_data_changes_only_at_container_end(self):
        parser = StellwerkSimProtocolParser()
        parser.feed_line(b"<zugliste><zug zid='1' name='Alt' /></zugliste>")
        parser.feed_line(b"<zugliste>")
        parser.feed_line(b"<zug zid='2' name='Neu' />")
        self.assertEqual(parser.trains[0].name, "Alt")
        result = parser.feed_line(b"</zugliste>")
        self.assertEqual(result.state, "complete")
        self.assertEqual([(item.name, item.zid) for item in parser.trains], [("Neu", "2")])

    def test_other_multiline_container_is_not_reported_as_error(self):
        parser = StellwerkSimProtocolParser()
        results = [
            parser.feed_line(b"<bahnsteigliste>"),
            parser.feed_line(b"<bahnsteig name='1' />"),
            parser.feed_line(b"</bahnsteigliste>"),
        ]
        self.assertEqual([result.state for result in results], ["pending", "pending", "complete"])
        self.assertTrue(all(result.error is None for result in results))


class FakeLog:
    def __init__(self):
        self.calls = []

    def configure(self, **kwargs):
        self.calls.append(("configure", kwargs))

    def delete(self, start, end):
        self.calls.append(("delete", start, end))


class ClearLogTests(unittest.TestCase):
    def test_clear_log_temporarily_enables_disabled_widget(self):
        gui = object.__new__(StellwerkSimTesterGUI)
        gui.log = FakeLog()
        gui.clear_log()
        self.assertEqual(gui.log.calls[0], ("configure", {"state": "normal"}))
        self.assertEqual(gui.log.calls[1][0:2], ("delete", "1.0"))
        self.assertEqual(gui.log.calls[2], ("configure", {"state": "disabled"}))


if __name__ == "__main__":
    unittest.main()
