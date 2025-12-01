"use client";

import {
  AssistantRuntimeProvider,
  AssistantTransportConnectionMetadata,
  makeAssistantTool,
  unstable_createMessageConverter as createMessageConverter,
  useAssistantTransportRuntime,
} from "@assistant-ui/react";
import {
  convertLangChainMessages,
  LangChainMessage,
} from "@assistant-ui/react-langgraph";
import { ReactNode } from "react";
import { z } from "zod";

import { makeAssistantToolUI } from '@assistant-ui/react';
import { DataTable, parseSerializableDataTable } from '@/components/tool-ui/data-table';
import { Loader2 } from "lucide-react";


export const SearchProductsUI = makeAssistantToolUI({
  toolName: 'search_products',
  render: ({ result }) => {

    // Loading state - spinner with box and query display
    if (!result) {
      return (
        <div className="flex min-h-[68px] items-center gap-3 rounded-md border-2 border-blue-400 bg-muted/50 p-3">
          <Loader2 className="h-5 w-5 animate-spin text-blue-500" />
          <span>Searching Products in Mercari Japan</span>
        </div>
      );
    }

    // Success state - render DataTable
    const parsedResult = typeof result === 'string' ? JSON.parse(result) : result;
    const props = parseSerializableDataTable(parsedResult);
    return <DataTable rowIdKey="id" {...props} />;
  },
});

type MyRuntimeProviderProps = {
  children: ReactNode;
};

type State = {
  messages: LangChainMessage[];
};

// Create LangChain message converter
const LangChainMessageConverter = createMessageConverter(
  convertLangChainMessages,
);

const converter = (
  state: State,
  connectionMetadata: AssistantTransportConnectionMetadata,
) => {
  // Build optimistic messages from pending commands
  const optimisticStateMessages = connectionMetadata.pendingCommands.map(
    (c): LangChainMessage[] => {
      if (c.type === "add-message") {
        return [
          {
            type: "human" as const,
            content: c.message.parts
              .map((p) => (p.type === "text" ? p.text : ""))
              .join("\n"),
          },
        ];
      }
      return [];
    },
  );

  const messages = [...(state?.messages || []), ...optimisticStateMessages.flat()];
  return {
    messages: LangChainMessageConverter.toThreadMessages(messages),
    isRunning: connectionMetadata.isSending || false,
  };
};

export function MyRuntimeProvider({ children }: MyRuntimeProviderProps) {
  const runtime = useAssistantTransportRuntime({
    initialState: {
      messages: [],
    },
    api:
      process.env["NEXT_PUBLIC_API_URL"] || "http://localhost:8001/assistant",
   // protocol: "data-stream",   
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
      <SearchProductsUI />

      {children}
    </AssistantRuntimeProvider>
  );
}
