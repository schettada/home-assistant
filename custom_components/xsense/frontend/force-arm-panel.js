class XSenseForceArmPanel extends HTMLElement {
  set hass(value) {
    this._hass = value;
    const language = value.language || value.locale?.language || "en";
    if (language !== this._language) {
      this._language = language;
      this.loadTranslations(language);
    }
    this.runAction();
  }

  connectedCallback() {
    this._connected = true;
    this.render();
    this.runAction();
  }

  disconnectedCallback() {
    this._connected = false;
    window.clearTimeout(this._backTimer);
  }

  async loadTranslations(language) {
    try {
      const replies = await Promise.all(["services", "exceptions", "selector"].map(
        (category) => this._hass.callWS({
          type: "frontend/get_translations",
          language,
          category,
          integration: ["xsense"],
        }),
      ));
      if (language !== this._language) return;
      this._translations = Object.assign({}, ...replies.map((reply) => reply.resources));
      this._translationError = null;
    } catch (error) {
      if (language !== this._language) return;
      this._translationError = error;
    }
    this.render();
  }

  text(key, placeholders = {}) {
    const fallback = {
      "services.force_arm.name": "Force arm",
      "services.force_arm.description": "Confirm a pending Home or Away arm request blocked by open sensors. Open sensors are bypassed.",
      "exceptions.force_arm_invalid_link.message": "This X-Sense force-arm link is invalid.",
      "exceptions.force_arm_request_failed.message": "The X-Sense force-arm request failed.",
      "selector.force_arm_mode.options.home": "Home",
      "selector.force_arm_mode.options.away": "Away",
    };
    let text = this._translations?.["component.xsense." + key] || fallback[key] || "";
    for (const [name, value] of Object.entries(placeholders)) {
      text = text.replaceAll("{" + name + "}", String(value));
    }
    return text;
  }

  async runAction() {
    if (!this._connected || !this._hass || this._started) return;

    const params = new URLSearchParams(window.location.hash.slice(1));
    const entityId = params.get("entity_id");
    const mode = params.get("mode");
    this._mode = mode;
    if (!entityId || !["Home", "Away"].includes(mode)) {
      this._status = "invalid";
      this.render();
      return;
    }

    this._started = true;
    this._status = "pending";
    this.render();
    try {
      await this._hass.callService("xsense", "force_arm", {
        entity_id: entityId,
        mode,
      });
      this._status = "submitted";
      this.render();
      if (this._connected) {
        this._backTimer = window.setTimeout(() => {
          if (window.history.length > 1) window.history.back();
          else window.location.assign("/");
        }, 800);
      }
    } catch (error) {
      this._status = "failed";
      this._error = error;
      this.render();
    }
  }

  render() {
    const failed = ["invalid", "failed"].includes(this._status);
    const title = this.text("services.force_arm.name") || "X-Sense";
    let message = this.text("services.force_arm.description");
    if (this._status === "invalid") {
      message = this.text("exceptions.force_arm_invalid_link.message");
    } else if (this._status === "submitted") {
      // Service acceptance is not confirmation that the station is armed.
      message += " (" + this.text("selector.force_arm_mode.options." + this._mode.toLowerCase()) + ")";
    } else if (this._status === "failed") {
      const error = this._error;
      message = error?.translation_domain === "xsense"
        ? this.text("exceptions." + error.translation_key + ".message", error.translation_placeholders)
        : "";
      message ||= error?.message || this.text("exceptions.force_arm_request_failed.message");
    }
    this.innerHTML = `
      <style>
        :host { display: block; min-height: 100%; background: var(--primary-background-color); color: var(--primary-text-color); }
        main { max-width: 560px; margin: 0 auto; padding: 48px 24px; text-align: center; overflow-wrap: anywhere; }
        h1 { font-size: 24px; letter-spacing: 0; margin: 0 0 16px; }
        p { line-height: 1.5; margin: 0; color: ${failed ? "var(--error-color)" : "var(--secondary-text-color)"}; }
        button { margin-top: 24px; border: 0; padding: 12px 20px; background: var(--primary-color); color: var(--text-primary-color); cursor: pointer; }
      </style>
      <main>
        <h1></h1>
        <p></p>
        ${failed ? '<button type="button"></button>' : ""}
      </main>`;
    this.querySelector("h1").textContent = title;
    this.querySelector("p").textContent = message;
    const back = this.querySelector("button");
    if (back) {
      back.textContent = this._hass?.localize?.("ui.common.back") || "Back";
      back.addEventListener("click", () => window.history.back());
    }
  }
}

customElements.define("xsense-force-arm-panel", XSenseForceArmPanel);
