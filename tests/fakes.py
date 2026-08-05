"""Lightweight Telegram fakes for handler tests (no live bot, no network)."""


class FakeUser:
    def __init__(self, user_id: int = 12345, username: str = "tester", first_name: str = "Tester"):
        self.id = user_id
        self.username = username
        self.first_name = first_name


class FakeChat:
    def __init__(self, chat_id: int = 12345):
        self.id = chat_id

    async def send_action(self, action: str) -> None:
        return None


class FakeMessage:
    """Mimics telegram.Message for reply_text/edit_text/chat access."""

    def __init__(self, text: str = "", chat=None):
        self.text = text
        self.chat = chat or FakeChat()
        self.calls: list[tuple] = []

    async def reply_text(self, text: str = "", **kwargs):
        self.calls.append(("reply", text, kwargs))
        return self

    async def edit_text(self, text: str = "", **kwargs):
        self.calls.append(("edit", text, kwargs))
        return self


class FakeCallbackQuery:
    def __init__(self, data: str, user=None, message=None):
        self.data = data
        self.from_user = user or FakeUser()
        self.message = message or FakeMessage()
        self.answers: list[tuple] = []

    async def answer(self, text: str | None = None, show_alert: bool = False) -> None:
        self.answers.append((text, show_alert))

    async def edit_message_text(self, text: str = "", **kwargs):
        self.message.calls.append(("edit", text, kwargs))
        return self.message


class FakeBot:
    def __init__(self):
        self.sent: list[tuple] = []

    async def send_message(self, chat_id, text: str = "", **kwargs):
        self.sent.append((chat_id, text, kwargs))
        return FakeMessage(text)


class FakeContext:
    """Mimics telegram.ext.ContextTypes.DEFAULT_TYPE for handler tests."""

    def __init__(self, args=None, bot=None):
        self.args = args or []
        self.bot = bot or FakeBot()
        self.user_data: dict = {}


class FakeUpdate:
    def __init__(self, message=None, callback_query=None, user=None):
        self.message = message
        self.callback_query = callback_query
        self._user = user

    @property
    def effective_user(self):
        if self._user is not None:
            return self._user
        if self.callback_query is not None:
            return self.callback_query.from_user
        return None

    @property
    def effective_chat(self):
        if self.message is not None:
            return self.message.chat
        if self.callback_query is not None and self.callback_query.message is not None:
            return self.callback_query.message.chat
        return None


def make_message_update(args, user_id: int = 12345):
    """Build (update, ctx, msg) for a command-style message."""
    user = FakeUser(user_id=user_id)
    msg = FakeMessage(chat=FakeChat(chat_id=user_id))
    update = FakeUpdate(message=msg, user=user)
    ctx = FakeContext(args=args)
    return update, ctx, msg
