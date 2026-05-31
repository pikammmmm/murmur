from murmur_sidecar.injector import type_text


class FakeController:
    def __init__(self):
        self.typed = []

    def type(self, text):
        self.typed.append(text)


def test_types_text():
    c = FakeController()
    type_text("hello world", controller=c)
    assert c.typed == ["hello world"]


def test_empty_and_none_are_noops():
    c = FakeController()
    type_text("", controller=c)
    type_text(None, controller=c)
    assert c.typed == []
