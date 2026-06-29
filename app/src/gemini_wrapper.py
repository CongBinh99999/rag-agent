from langchain_core.messages import HumanMessage, SystemMessage, AIMessage

class GeminiOpenAIWrapper:
    """Adapter to wrap LangChain's ChatGoogleGenerativeAI (gemini-3.1-flash-lite)
    into an OpenAI-compatible client interface for MarkItDown and markitdown-ocr.
    """
    def __init__(self, llm):
        self.llm = llm
        self.chat = self
        self.completions = self

    class Choice:
        class Message:
            def __init__(self, content):
                self.content = content
        def __init__(self, content):
            self.message = self.Message(content)

    class Response:
        def __init__(self, content):
            self.choices = [GeminiOpenAIWrapper.Choice(content)]

    def create(self, model, messages, **kwargs):
        lc_messages = []
        for msg in messages:
            role = msg.get("role")
            content = msg.get("content")
            
            # Content can be a string or a list (multimodal image format)
            if isinstance(content, list):
                formatted_content = []
                for item in content:
                    item_type = item.get("type")
                    if item_type == "text":
                        formatted_content.append({"type": "text", "text": item.get("text")})
                    elif item_type == "image_url":
                        url_data = item.get("image_url", {}).get("url", "")
                        formatted_content.append({
                            "type": "image_url",
                            "image_url": {"url": url_data}
                        })
                content = formatted_content
                
            if role == "system":
                lc_messages.append(SystemMessage(content=content))
            elif role == "user":
                lc_messages.append(HumanMessage(content=content))
            elif role == "assistant":
                lc_messages.append(AIMessage(content=content))
                
        # Invoke the model
        response = self.llm.invoke(lc_messages)
        
        # Clean response content if it is returned as a LangChain list block
        content_text = response.content
        if isinstance(content_text, list):
            content_text = "".join(item.get("text", "") for item in content_text if isinstance(item, dict))
            
        return self.Response(content_text)
