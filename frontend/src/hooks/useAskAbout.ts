import { useCallback, useState } from "react";
import { useNavigate } from "react-router-dom";

import { useToast } from "../components/ui/Toast";
import { createConversation } from "../services/conversationService";

/** Opens a new conversation, optionally limited to some documents for its first question. */
export interface AskScope {
  [documentId: string]: string; // document id -> name
}

export function useAskAbout() {
  const navigate = useNavigate();
  const toast = useToast();
  const [busy, setBusy] = useState(false);

  const ask = useCallback(
    async (scope?: AskScope) => {
      setBusy(true);
      try {
        const created = await createConversation();
        navigate(`/workspace/${created.id}`, { state: scope ? { scope } : undefined });
      } catch (error) {
        toast.error(error);
      } finally {
        setBusy(false);
      }
    },
    [navigate, toast],
  );

  return { ask, busy };
}
