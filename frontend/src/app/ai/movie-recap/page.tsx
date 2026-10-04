import { redirect } from "next/navigation";

// This stand-alone tool page was a design preview; the feature is a workflow template on the Create page.
export default function Page() {
  redirect("/create");
}
