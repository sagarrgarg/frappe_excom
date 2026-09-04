import { useFrappeGetCall } from "@/lib/api";

export interface ExcomBranding {
  logo_gradient_from: string;
  logo_gradient_to: string;
  show_app_name: boolean;
  app_name: string;
}

/** Excom sidebar / header branding from Excom Settings + Website Settings. */
export function useExcomBranding() {
  const { data, error, isValidating } = useFrappeGetCall<{
    message: ExcomBranding;
  }>("excom.excom.doctype.excom_settings.excom_settings.get_branding");

  return {
    branding: data?.message,
    error,
    isLoading: isValidating && !data,
  };
}
