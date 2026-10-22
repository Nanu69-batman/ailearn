import type { Metadata } from "next";
import DevConsole from "@/components/dev/DevConsole";

export const metadata: Metadata = {
  title: "Developer view · Malai Pioneer",
  description: "Live, animated trace of ingestion, AI tutor and quiz pipelines.",
};

export default function DevPage() {
  return <DevConsole />;
}
