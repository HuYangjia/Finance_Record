(() => {
  const openPickers = new Set();

  function optionActivity(option, activityKey) {
    try {
      const activity = JSON.parse(option.dataset.activity || "{}");
      const keyedActivity = activity[activityKey];
      if (keyedActivity && typeof keyedActivity === "object") {
        return keyedActivity;
      }
      if (Number.isFinite(Number(activity.record_count))) {
        return activity;
      }
      return { record_count: 0, last_transaction_date: null };
    } catch (_) {
      return { record_count: 0, last_transaction_date: null };
    }
  }

  function activityScore(activity) {
    const recordCount = Number(activity.record_count) || 0;
    if (!Object.hasOwn(activity, "recent_record_count")) {
      return recordCount;
    }
    return recordCount + (Number(activity.recent_record_count) || 0) * 5;
  }

  function compareActivity(left, right) {
    return (
      activityScore(right) - activityScore(left)
      || (Number(right.recent_record_count) || 0) - (Number(left.recent_record_count) || 0)
      || (Number(right.record_count) || 0) - (Number(left.record_count) || 0)
      || (right.last_transaction_date || "").localeCompare(
        left.last_transaction_date || "",
      )
    );
  }

  function optionButton(option, selectOption) {
    const button = document.createElement("button");
    button.className = "frequent-picker-option";
    button.type = "button";
    button.textContent = option.textContent;
    button.dataset.value = option.value;
    button.disabled = option.disabled;
    button.classList.toggle("is-selected", option.selected);
    button.addEventListener("click", () => selectOption(option.value));
    return button;
  }

  function createFrequentPicker(select, options = {}) {
    if (select._frequentPicker) {
      select._frequentPicker.configure(options);
      select._frequentPicker.refresh();
      return select._frequentPicker;
    }

    const configuration = {
      commonLabel: "常用选项",
      otherLabel: "其他选项",
      commonLimit: 6,
      activityKey: "overall",
      displayMode: "common-other",
      visibleRows: 8,
      ...options,
    };
    const originalRequired = select.required;
    const wrapper = document.createElement("div");
    const trigger = document.createElement("button");
    const menu = document.createElement("div");
    let required = originalRequired;
    let otherExpanded = false;

    wrapper.className = "frequent-picker";
    trigger.className = "frequent-picker-trigger";
    trigger.type = "button";
    trigger.setAttribute("aria-expanded", "false");
    menu.className = "frequent-picker-menu";
    menu.hidden = true;
    select.classList.add("frequent-picker-native");
    select.required = false;
    select.insertAdjacentElement("afterend", wrapper);
    wrapper.append(trigger, menu);

    function selectedOption() {
      return Array.from(select.options).find((option) => option.selected) || null;
    }

    function close() {
      menu.hidden = true;
      trigger.setAttribute("aria-expanded", "false");
      openPickers.delete(controller);
    }

    function choose(value) {
      select.value = value;
      select.dispatchEvent(new Event("change", { bubbles: true }));
      wrapper.classList.remove("is-invalid");
      close();
      refresh();
    }

    function appendGroup(title, records) {
      if (!records.length) {
        return;
      }
      const heading = document.createElement("p");
      heading.className = "frequent-picker-group-label";
      heading.textContent = title;
      menu.append(heading);
      for (const option of records) {
        menu.append(optionButton(option, choose));
      }
    }

    function rankedRecords(usedOnly = true) {
      return Array.from(select.options)
        .filter((option) => option.value !== "")
        .map((option, index) => ({
          option,
          index,
          activity: optionActivity(option, configuration.activityKey),
        }))
        .filter((item) => !usedOnly || item.activity.record_count > 0)
        .sort((left, right) => (
          compareActivity(left.activity, right.activity)
          || left.index - right.index
        ));
    }

    function renderMenu() {
      menu.replaceChildren();
      const allOptions = Array.from(select.options);
      const emptyOption = allOptions.find((option) => option.value === "");
      const records = allOptions.filter((option) => option.value !== "");
      if (configuration.displayMode === "ranked-scroll") {
        wrapper.classList.add("is-ranked-scroll");
        menu.style.setProperty("--picker-visible-rows", configuration.visibleRows);
        if (emptyOption && !emptyOption.disabled) {
          menu.append(optionButton(emptyOption, choose));
        }
        for (const item of rankedRecords(false)) {
          menu.append(optionButton(item.option, choose));
        }
        return;
      }
      wrapper.classList.remove("is-ranked-scroll");
      menu.style.removeProperty("--picker-visible-rows");
      const common = rankedRecords()
        .slice(0, configuration.commonLimit)
        .map((item) => item.option);
      const commonValues = new Set(common.map((option) => option.value));
      const other = records.filter((option) => !commonValues.has(option.value));

      if (emptyOption && !emptyOption.disabled) {
        menu.append(optionButton(emptyOption, choose));
      }
      appendGroup(configuration.commonLabel, common);

      if (other.length) {
        if (!common.length) {
          appendGroup(`全部${configuration.otherLabel}`, other);
          return;
        }
        const otherToggle = document.createElement("button");
        otherToggle.className = "frequent-picker-other-toggle";
        otherToggle.type = "button";
        otherToggle.textContent = otherExpanded
          ? `收起其他${configuration.otherLabel}`
          : `其他${configuration.otherLabel}（${other.length}）`;
        otherToggle.addEventListener("click", (event) => {
          event.stopPropagation();
          otherExpanded = !otherExpanded;
          renderMenu();
          menu.querySelector(".frequent-picker-other-toggle")?.focus();
        });
        menu.append(otherToggle);
        if (otherExpanded) {
          appendGroup(`其他${configuration.otherLabel}`, other);
        }
      }
    }

    function refresh() {
      const selected = selectedOption();
      trigger.textContent = selected?.textContent || "请选择";
      trigger.disabled = select.disabled;
      trigger.setAttribute("aria-required", String(required && !select.disabled));
      wrapper.classList.toggle("is-disabled", select.disabled);
      renderMenu();
    }

    function open() {
      if (trigger.disabled) {
        return;
      }
      for (const picker of [...openPickers]) {
        if (picker !== controller) {
          picker.close();
        }
      }
      renderMenu();
      menu.hidden = false;
      trigger.setAttribute("aria-expanded", "true");
      openPickers.add(controller);
      const selected = selectedOption();
      const selectedButton = selected
        ? menu.querySelector(`[data-value="${CSS.escape(selected.value)}"]`)
        : null;
      const firstOption = menu.querySelector("button:not(:disabled)");
      (selectedButton || firstOption)?.focus();
      selectedButton?.scrollIntoView({ block: "nearest" });
    }

    function validate() {
      const valid = select.disabled || !required || Boolean(select.value);
      wrapper.classList.toggle("is-invalid", !valid);
      if (!valid) {
        trigger.focus();
      }
      return valid;
    }

    const controller = {
      close,
      contains(target) {
        return wrapper.contains(target);
      },
      configure(nextOptions = {}) {
        Object.assign(configuration, nextOptions);
      },
      preferredValue() {
        return rankedRecords()[0]?.option.value || "";
      },
      refresh,
      setRequired(value) {
        required = Boolean(value);
        select.required = false;
        refresh();
      },
      validate,
    };
    select._frequentPicker = controller;

    trigger.addEventListener("click", () => {
      if (menu.hidden) {
        open();
      } else {
        close();
      }
    });
    trigger.addEventListener("keydown", (event) => {
      if (event.key !== "ArrowDown" && event.key !== "ArrowUp") {
        return;
      }
      event.preventDefault();
      open();
      const firstOption = menu.querySelector("button:not(:disabled)");
      firstOption?.focus();
    });
    wrapper.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        close();
        trigger.focus();
        return;
      }
      if (event.key !== "ArrowDown" && event.key !== "ArrowUp") {
        return;
      }
      const buttons = Array.from(menu.querySelectorAll("button:not(:disabled)"));
      if (!buttons.length) {
        return;
      }
      event.preventDefault();
      const current = document.activeElement;
      const index = buttons.indexOf(current);
      const nextIndex = event.key === "ArrowDown"
        ? Math.min(index + 1, buttons.length - 1)
        : Math.max(index - 1, 0);
      buttons[nextIndex < 0 ? 0 : nextIndex].focus();
    });

    refresh();
    return controller;
  }

  document.addEventListener("click", (event) => {
    for (const picker of [...openPickers]) {
      if (!picker.contains(event.target)) {
        picker.close();
      }
    }
  });

  window.createFrequentPicker = createFrequentPicker;
  window.refreshFrequentPickers = (root = document) => {
    for (const select of root.querySelectorAll("select")) {
      select._frequentPicker?.refresh();
    }
  };
  window.setFrequentPickerRequired = (select, required) => {
    select._frequentPicker?.setRequired(required);
  };
  window.validateFrequentPickers = (form) => Array.from(
    form.querySelectorAll("select"),
  ).every((select) => select._frequentPicker?.validate() ?? true);
})();
