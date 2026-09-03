import { useFrappeGetDocList } from "frappe-react-sdk";

/** Companies for the rail switcher. Hidden when the user has one (UX-001 §3.3). */
export function useCompanies() {
  const { data } = useFrappeGetDocList<{ name: string; abbr: string }>("Company", {
    fields: ["name", "abbr"],
    limit: 50,
    orderBy: { field: "name", order: "asc" },
  });
  return { companies: data ?? [] };
}
