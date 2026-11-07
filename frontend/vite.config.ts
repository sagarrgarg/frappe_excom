import path from "path";
import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";
import { VitePWA } from "vite-plugin-pwa";
import proxyOptions from "./proxyOptions";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  return {
    plugins: [
      react(),
      VitePWA({
        registerType: "autoUpdate",
        strategies: "injectManifest",
        injectRegister: null,
        srcDir: "public",
        filename: "sw.js",
        outDir: "../excom/public/excom",
        manifest: {
          name: "Excom",
          short_name: "Excom",
          // Inside the scope: "/excom" is not under "/excom/", and Chrome then drops the scope, so the
          // installed app treated the whole site (Desk too) as itself.
          start_url: "/excom/inbox",
          scope: "/excom/",
          description: "Omnichannel External Communication Platform",
          display: "standalone",
          background_color: "#0b141a",
          theme_color: "#0b141a",
          icons: [
            {
              src: "/assets/excom/excom/manifest/android-chrome-192x192.png",
              sizes: "192x192",
              type: "image/png",
            },
            {
              src: "/assets/excom/excom/manifest/android-chrome-512x512.png",
              sizes: "512x512",
              type: "image/png",
            },
            {
              src: "/assets/excom/excom/manifest/apple-touch-icon.png",
              sizes: "180x180",
              type: "image/png",
            },
            {
              src: "/assets/excom/excom/manifest/favicon-96x96.png",
              sizes: "96x96",
              type: "image/png",
            },
            {
              src: "/assets/excom/excom/manifest/favicon-32x32.png",
              sizes: "32x32",
              type: "image/png",
            },
          ],
        },
      }),
    ],
    server: {
      port: 8081,
      proxy: proxyOptions,
    },
    resolve: {
      alias: {
        "@": path.resolve(__dirname, "./src"),
      },
    },
    build: {
      outDir: "../excom/public/excom",
      emptyOutDir: true,
      target: "es2015",
      rollupOptions: {
        onwarn(warning, warn) {
          if (warning.code === "MODULE_LEVEL_DIRECTIVE") return;
          warn(warning);
        },
      },
    },
  };
});
