"""WhatsAppPort implementation using WhatsApp Cloud API."""


class WhatsAppCloudAdapter:
    async def send_template(self, to: str, template_name: str, params: dict) -> dict:
        raise NotImplementedError

    async def send_message(self, to: str, content: str, from_number: str) -> dict:
        raise NotImplementedError

    async def handle_inbound(self, webhook_payload: dict) -> dict:
        raise NotImplementedError
