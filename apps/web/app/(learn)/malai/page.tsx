import { Suspense } from "react";
import MalaiChatView from "@/components/MalaiChatView";

export const metadata = {
  title: "Malai — Malai Pioneer",
  description: "Your learning companion: ask anything about your subjects.",
};

/**
 * /malai (new chat) and /malai?c=<chat id> (an existing one). One route on
 * purpose: switching chats changes only the query string, so the page and
 * the sidebar above it are never remounted.
 */
export default function MalaiPage() {
  return (
    <Suspense fallback={null}>
      <MalaiChatView />
    </Suspense>
  );
}
