"use client";

import {
  AssistantRuntimeProvider,
  AssistantTransportConnectionMetadata,
  makeAssistantTool,
  useAssistantTransportRuntime,
  ThreadMessage,
  ThreadUserMessage,
  ThreadAssistantMessage,
  TextMessagePart,
  ToolCallMessagePart,
} from "@assistant-ui/react";
import { ReactNode } from "react";
import { z } from "zod";

import { makeAssistantToolUI } from '@assistant-ui/react';
import { DataTable, parseSerializableDataTable } from '@/components/tool-ui/data-table';

// Frontend tool with execute function
const WeatherTool = makeAssistantTool({
  type: "frontend",
  toolName: "web_search",
  description: "Get the current weather for a city",
  parameters: z.object({
    location: z.string().describe("The city to get weather for"),
    unit: z
      .enum(["celsius", "fahrenheit"])
      .optional()
      .describe("Temperature unit"),
  }),
  execute: async ({ location, unit = "celsius" }) => {
    console.log(`Getting weather for ${location} in ${unit}`);
    // Simulate API call
    await new Promise((resolve) => setTimeout(resolve, 1000));

    const temp = Math.floor(Math.random() * 30) + 10;
    const conditions = ["sunny", "cloudy", "rainy", "partly cloudy"];
    const condition = conditions[Math.floor(Math.random() * conditions.length)];

    return {
      location,
      temperature: temp,
      unit,
      condition,
      humidity: Math.floor(Math.random() * 40) + 40,
      windSpeed: Math.floor(Math.random() * 20) + 5,
    };
  },
  streamCall: async (reader) => {
    console.log("streamCall", reader);
    const city = await reader.args.get("location");
    console.log("location", city);

    const args = await reader.args.get();
    console.log("args", args);

    const result = await reader.response.get();
    console.log("result", result);
  },
});

export const SearchProductsUI = makeAssistantToolUI({
  toolName: 'searchProducts',
  render: ({ result }) => {
    // Handle loading state when result is not yet available
    if (!result) {
      return (
        <DataTable
          rowIdKey="id"
          surfaceId="mercari-search-loading"
          columns={[
            { key: 'name', label: 'Product', priority: 'primary' },
            { key: 'price', label: 'Price' },
            { key: 'condition', label: 'Condition' },
            { key: 'seller', label: 'Seller' },
            { key: 'url', label: 'Link' },
          ]}
          data={[]}
          isLoading={true}
        />
      );
    }
    // Parse JSON string if result is a string
    const parsedResult = typeof result === 'string' ? JSON.parse(result) : result;
    const props = parseSerializableDataTable(parsedResult);
    return <DataTable rowIdKey="id" {...props} />;
  },
});

type MyRuntimeProviderProps = {
  children: ReactNode;
};

// Message types matching backend output (Anthropic format)
type ContentBlock =
  | { type: "text"; text: string }
  | { type: "tool_use"; id: string; name: string; input: Record<string, unknown> }
  | { type: "tool_result"; tool_use_id: string; content: string };

type BackendMessage = {
  role: "user" | "assistant";
  content: string | ContentBlock[];
};

type State = {
  messages: BackendMessage[];
};

// Helper to create default metadata
const createDefaultMetadata = () => ({
  unstable_state: undefined,
  unstable_annotations: undefined,
  unstable_data: undefined,
  steps: undefined,
  submittedFeedback: undefined,
  custom: {},
});

// Convert backend messages to ThreadMessages for assistant-ui
function convertToThreadMessages(messages: BackendMessage[]): ThreadMessage[] {
  const result: ThreadMessage[] = [];
  const now = new Date();

  for (let i = 0; i < messages.length; i++) {
    const msg = messages[i];
    const id = `msg-${i}`;

    if (msg.role === "user") {
      // User message - could be text or tool result
      if (typeof msg.content === "string") {
        const userMsg: ThreadUserMessage = {
          id,
          createdAt: now,
          role: "user",
          content: [{ type: "text", text: msg.content } as TextMessagePart],
          attachments: [],
          metadata: createDefaultMetadata(),
        };
        result.push(userMsg);
      } else if (Array.isArray(msg.content)) {
        // Handle tool results in user messages
        const toolResults = msg.content.filter(
          (block): block is Extract<ContentBlock, { type: "tool_result" }> =>
            block.type === "tool_result"
        );
        // Skip tool result messages as they'll be attached to tool calls
        if (toolResults.length === 0) {
          // Regular content blocks
          const textContent = msg.content
            .filter((block): block is Extract<ContentBlock, { type: "text" }> => block.type === "text")
            .map((block) => block.text)
            .join("\n");
          if (textContent) {
            const userMsg: ThreadUserMessage = {
              id,
              createdAt: now,
              role: "user",
              content: [{ type: "text", text: textContent } as TextMessagePart],
              attachments: [],
              metadata: createDefaultMetadata(),
            };
            result.push(userMsg);
          }
        }
      }
    } else if (msg.role === "assistant") {
      // Assistant message - could have text and/or tool calls
      const contentBlocks = Array.isArray(msg.content)
        ? msg.content
        : [{ type: "text" as const, text: String(msg.content) }];

      const threadContent: (TextMessagePart | ToolCallMessagePart)[] = [];

      for (const block of contentBlocks) {
        if (block.type === "text" && block.text) {
          threadContent.push({ type: "text", text: block.text } as TextMessagePart);
        } else if (block.type === "tool_use") {
          // Find the corresponding tool result in the next message
          const nextMsg = messages[i + 1];
          let toolResult: unknown = undefined;

          if (nextMsg && Array.isArray(nextMsg.content)) {
            const resultBlock = nextMsg.content.find(
              (b): b is Extract<ContentBlock, { type: "tool_result" }> =>
                b.type === "tool_result" && b.tool_use_id === block.id
            );
            if (resultBlock) {
              try {
                toolResult = JSON.parse(resultBlock.content);
              } catch {
                toolResult = resultBlock.content;
              }
            }
          }

          threadContent.push({
            type: "tool-call",
            toolCallId: block.id,
            toolName: block.name,
            args: block.input,
            argsText: JSON.stringify(block.input),
            result: toolResult,
            status: { type: "complete" },
          } as ToolCallMessagePart);
        }
      }

      if (threadContent.length > 0) {
        const assistantMsg: ThreadAssistantMessage = {
          id,
          createdAt: now,
          role: "assistant",
          content: threadContent,
          status: { type: "complete", reason: "stop" },
          metadata: {
            unstable_state: null,
            unstable_annotations: [],
            unstable_data: [],
            steps: [],
            submittedFeedback: undefined,
            custom: {},
          },
        };
        result.push(assistantMsg);
      }
    }
  }

  return result;
}

const converter = (
  state: State,
  connectionMetadata: AssistantTransportConnectionMetadata,
) => {
  // Build optimistic messages from pending commands
  const optimisticMessages: BackendMessage[] = connectionMetadata.pendingCommands.flatMap(
    (c) => {
      if (c.type === "add-message") {
        const text = c.message.parts
          .map((p) => (p.type === "text" ? p.text : ""))
          .join("\n");
        return [{ role: "user" as const, content: text }];
      }
      return [];
    },
  );

  const allMessages = [...(state?.messages || []), ...optimisticMessages];
  return {
    messages: convertToThreadMessages(allMessages),
    isRunning: connectionMetadata.isSending || false,
  };
};

export function MyRuntimeProvider({ children }: MyRuntimeProviderProps) {
  const runtime = useAssistantTransportRuntime({
    initialState: {
      messages: [],
    },
    api:
      process.env["NEXT_PUBLIC_API_URL"] || "http://localhost:8010/assistant",
    converter,
    headers: async () => ({
      "Test-Header": "test-value",
    }),
    body: {
      "Test-Body": "test-value",
    },
    onResponse: () => {
      console.log("Response received from server");
    },
    onFinish: () => {
      console.log("Conversation completed");
    },
    onError: (error: Error) => {
      console.error("Assistant transport error:", error);
    },
    onCancel: () => {
      console.log("Request cancelled");
    },
  });

  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <WeatherTool />
      <SearchProductsUI />

      {children}
    </AssistantRuntimeProvider>
  );
}
