import { redirect } from "next/navigation";

import { DEFAULT_PORTAL } from "@/lib/portals";

/** `/console` has no meaning on its own — send it to the first source. */
export default function ConsoleIndex() {
  redirect(`/console/${DEFAULT_PORTAL}`);
}
