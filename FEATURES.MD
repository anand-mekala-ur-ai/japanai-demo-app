implemented features
1. as per the requirements it was mentioned to use sonnet 3.5 model however i couldnt find the model anywhere, so i have used a similiar model sonnet-4.5 model refer to https://platform.claude.com/docs/en/about-claude/model-deprecations
2. mercari product search tool call capability
3. chat context management (like it will understand the previous queries)
4. used https://www.assistant-ui.com for building frontend
5. product search tool call will be having tool ui to show images
5. real-time streaming support for text aswell as fo tool calling (super interactive)
6. the merchaiewebsearch is handled by scrappingbee.com an exteral client accurate scrapped data


tech used
1. no high-level frameworks are using only using official anthropics sdk for calling the sonnet-4.5 model
2. 

how to improve it
1. currently the demo is working with only one tool call, however to implements a robust and extensive products search on mercuri japan capabilites..we have to devlop a mcp server with mutiple tools (multiple apis) in it.
2. for chat persistance we have to implement state management to keep the thread of every chat execution for this we can leverage langgraph thread or we can create our custom thread management by connecting to external db
3. the edit chat query and reload of chat response funtionality