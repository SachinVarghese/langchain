"""Prompt template that contains few shot examples."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, Extra, root_validator

from langchain.prompts.base import (
    DEFAULT_FORMATTER_MAPPING,
    StringPromptTemplate,
    check_valid_template,
)
from langchain.prompts.chat import BaseChatPromptTemplate, BaseMessagePromptTemplate
from langchain.prompts.example_selector.base import BaseExampleSelector
from langchain.prompts.prompt import PromptTemplate
from langchain.schema.messages import BaseMessage, get_buffer_string
from langchain.schema.prompt_template import BasePromptTemplate


class _FewShotPromptTemplateMixin(BaseModel):
    """Prompt template that contains few shot examples."""

    examples: Optional[List[dict]] = None
    """Examples to format into the prompt.
    Either this or example_selector should be provided."""

    example_selector: Optional[BaseExampleSelector] = None
    """ExampleSelector to choose the examples to format into the prompt.
    Either this or examples should be provided."""

    class Config:
        """Configuration for this pydantic object."""

        extra = Extra.forbid
        arbitrary_types_allowed = True

    @root_validator(pre=True)
    def check_examples_and_selector(cls, values: Dict) -> Dict:
        """Check that one and only one of examples/example_selector are provided."""
        examples = values.get("examples", None)
        example_selector = values.get("example_selector", None)
        if examples and example_selector:
            raise ValueError(
                "Only one of 'examples' and 'example_selector' should be provided"
            )

        if examples is None and example_selector is None:
            raise ValueError(
                "One of 'examples' and 'example_selector' should be provided"
            )

        return values

    def _get_examples(self, **kwargs: Any) -> List[dict]:
        """Get the examples to use for formatting the prompt.

        Args:
            **kwargs: Keyword arguments to be passed to the example selector.

        Returns:
            List of examples.
        """
        if self.examples is not None:
            return self.examples
        elif self.example_selector is not None:
            return self.example_selector.select_examples(kwargs)
        else:
            raise ValueError(
                "One of 'examples' and 'example_selector' should be provided"
            )


class FewShotPromptTemplate(_FewShotPromptTemplateMixin, StringPromptTemplate):
    """Prompt template that contains few shot examples."""

    @property
    def lc_serializable(self) -> bool:
        """Return whether the prompt template is lc_serializable.

        Returns:
            Boolean indicating whether the prompt template is lc_serializable.
        """
        return False

    validate_template: bool = True
    """Whether or not to try validating the template."""

    input_variables: List[str]
    """A list of the names of the variables the prompt template expects."""

    example_prompt: PromptTemplate
    """PromptTemplate used to format an individual example."""

    suffix: str
    """A prompt template string to put after the examples."""

    example_separator: str = "\n\n"
    """String separator used to join the prefix, the examples, and suffix."""

    prefix: str = ""
    """A prompt template string to put before the examples."""

    template_format: str = "f-string"
    """The format of the prompt template. Options are: 'f-string', 'jinja2'."""

    @root_validator()
    def template_is_valid(cls, values: Dict) -> Dict:
        """Check that prefix, suffix, and input variables are consistent."""
        if values["validate_template"]:
            check_valid_template(
                values["prefix"] + values["suffix"],
                values["template_format"],
                values["input_variables"] + list(values["partial_variables"]),
            )
        return values

    class Config:
        """Configuration for this pydantic object."""

        extra = Extra.forbid
        arbitrary_types_allowed = True

    def format(self, **kwargs: Any) -> str:
        """Format the prompt with the inputs.

        Args:
            **kwargs: Any arguments to be passed to the prompt template.

        Returns:
            A formatted string.

        Example:

        .. code-block:: python

            prompt.format(variable1="foo")
        """
        kwargs = self._merge_partial_and_user_variables(**kwargs)
        # Get the examples to use.
        examples = self._get_examples(**kwargs)
        examples = [
            {k: e[k] for k in self.example_prompt.input_variables} for e in examples
        ]
        # Format the examples.
        example_strings = [
            self.example_prompt.format(**example) for example in examples
        ]
        # Create the overall template.
        pieces = [self.prefix, *example_strings, self.suffix]
        template = self.example_separator.join([piece for piece in pieces if piece])

        # Format the template with the input variables.
        return DEFAULT_FORMATTER_MAPPING[self.template_format](template, **kwargs)

    @property
    def _prompt_type(self) -> str:
        """Return the prompt type key."""
        return "few_shot"

    def dict(self, **kwargs: Any) -> Dict:
        """Return a dictionary of the prompt."""
        if self.example_selector:
            raise ValueError("Saving an example selector is not currently supported")
        return super().dict(**kwargs)


class FewShotChatMessagePromptTemplate(
    BaseChatPromptTemplate, _FewShotPromptTemplateMixin
):
    """Chat prompt template that supports few-shot examples.

    The high level structure of produced by this prompt template is a list of messages
    consisting of prefix message(s), example message(s), and suffix message(s).

    This structure enables creating a conversation with intermediate examples like:

        System: You are a helpful AI Assistant
        Human: What is 2+2?
        AI: 4
        Human: What is 2+3?
        AI: 5
        Human: What is 4+4?

    This prompt template can be used to generate a fixed list of examples or else
    to dynamically select examples based on the input.

    Examples:

        Prompt template with a fixed list of examples (matching the sample
        conversation above):

        .. code-block:: python

            from langchain.schema import SystemMessage
            from langchain.prompts import (
                ChatPromptTemplate,
                FewShotChatMessagePromptTemplate,
                HumanMessagePromptTemplate,
                AIMessagePromptTemplate
            )

            examples = [
                {"input": "2+2", "output": "4"},
                {"input": "2+3", "output": "5"},
            ]

            # This is a prompt template used to format each individual example.
            example_prompt = ChatPromptTemplate.from_messages(
                [
                    HumanMessagePromptTemplate.from_template("{input}"),
                    AIMessagePromptTemplate.from_template("{output}"),
                ]
            )

            few_shot_prompt = FewShotChatMessagePromptTemplate(
                input_variables=["input"],
                prefix=[SystemMessage(content="You are a helpful AI Assistant")],
                example_prompt=example_prompt,
                examples=examples,
                suffix=[HumanMessagePromptTemplate.from_template("{input}")],
            )

            few_shot_prompt.format(input="What is 4+4?")

        Prompt template with dynamically selected examples:

        .. code-block:: python

            from langchain.prompts import SemanticSimilarityExampleSelector
            from langchain.embeddings import OpenAIEmbeddings
            from langchain.vectorstores import Chroma

            examples = [
                {"input": "2+2", "output": "4"},
                {"input": "2+3", "output": "5"},
                {"input": "2+4", "output": "6"},
                # ...
            ]

            to_vectorize = [
                " ".join(example.values())
                for example in examples
            ]
            embeddings = OpenAIEmbeddings()
            vectorstore = Chroma.from_texts(
                to_vectorize, embeddings, metadatas=examples
            )
            example_selector = SemanticSimilarityExampleSelector(
                vectorstore=vectorstore
            )

            from langchain.schema import SystemMessage
            from langchain.prompts import ChatPromptTemplate, HumanMessagePromptTemplate
            from langchain.prompts.few_shot import FewShotChatMessagePromptTemplate

            # Define how each example will be formatted.
            # In this case, each example will become 2 messages:
            # 1 human, and 1 AI
            example_prompt = ChatPromptTemplate.from_messages(
                [
                    HumanMessagePromptTemplate.from_template("{input}"),
                    AIMessagePromptTemplate.from_template("{output}"),
                ]
            )

            # Define the overall prompt.
            few_shot_prompt = FewShotChatMessagePromptTemplate(
                input_variables=["input"],
                prefix = [SystemMessage(content="You are a helpful AI Assistant")],
                example_selector=example_selector,
                example_prompt=example_prompt,
                suffix = [HumanMessagePromptTemplate.from_template("{input}")],
            )
            # Show the prompt
            print(few_shot_prompt.format_messages(input="What's 3+3?"))

            # Use within an LLMChain
            from langchain.chains import LLMChain
            from langchain.chat_models import ChatOpenAI
            chain = LLMChain(
                llm=ChatOpenAI(),
                prompt=few_shot_prompt,
            )
            chain({"input": "What's 3+3?"})
    """

    @property
    def lc_serializable(self) -> bool:
        """Return whether the prompt template is lc_serializable.

        Returns:
            Boolean indicating whether the prompt template is lc_serializable.
        """
        return False

    prefix: List[
        Union[BaseMessagePromptTemplate, BaseChatPromptTemplate, BaseMessage]
    ] = []
    """The class to format the prefix."""
    example_prompt: Union[BaseMessagePromptTemplate, BaseChatPromptTemplate]
    """The class to format each example."""
    suffix: List[
        Union[BaseMessagePromptTemplate, BaseChatPromptTemplate, BaseMessage]
    ] = []
    """The class to format the suffix."""

    class Config:
        """Configuration for this pydantic object."""

        extra = Extra.forbid
        arbitrary_types_allowed = True

    def format_messages(self, **kwargs: Any) -> List[BaseMessage]:
        """Format kwargs into a list of messages.

        Args:
            **kwargs: keyword arguments to use for filling in templates in messages.

        Returns:
            A list of formatted messages with all template variables filled in.
        """
        # Get the examples to use.
        examples = self._get_examples(**kwargs)
        examples = [
            {k: e[k] for k in self.example_prompt.input_variables} for e in examples
        ]
        # Format prefix examples
        prefix_messages = [
            message
            for template in self.prefix
            for message in (
                template.format_messages(**kwargs)
                if isinstance(template, (BasePromptTemplate, BaseMessagePromptTemplate))
                else [template]
            )
        ]
        # Format the examples.
        messages = [
            message
            for example in examples
            for message in self.example_prompt.format_messages(**example)
        ]
        # Format suffix examples
        suffix_messages = [
            message
            for template in self.suffix
            for message in (
                template.format_messages(**kwargs)
                if isinstance(template, (BasePromptTemplate, BaseMessagePromptTemplate))
                else [template]
            )
        ]
        return prefix_messages + messages + suffix_messages

    def format(self, **kwargs: Any) -> str:
        """Format the prompt with inputs generating a string.

        Use this method to generate a string representation of a prompt consisting
        of chat messages.

        Useful for feeding into a string based completion language model or debugging.

        Args:
            **kwargs: keyword arguments to use for formatting.

        Returns:
            A string representation of the prompt
        """
        messages = self.format_messages(**kwargs)
        return get_buffer_string(messages)
