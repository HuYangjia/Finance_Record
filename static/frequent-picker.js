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

  function optionButton(option, selectOption, className = "") {
    const button = document.createElement("button");
    button.className = `frequent-picker-option ${className}`.trim();
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
    const expandedDirectories = new Set();
    const expandedInstitutions = new Set();

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
      otherExpanded = false;
      expandedDirectories.clear();
      expandedInstitutions.clear();
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

    function directoryRecord(option, index) {
      return {
        option,
        index,
        directoryKey: option.dataset.directoryKey || "other",
        directoryName: option.dataset.directoryName || "其他账户",
        directoryOrder: Number(option.dataset.directoryOrder) || 90,
        institution: option.dataset.institution || "其他机构",
        currency: option.dataset.currency || "",
      };
    }

    function institutionKey(directoryKey, institution) {
      return `${directoryKey}/${encodeURIComponent(institution)}`;
    }

    function directoryRecords() {
      return Array.from(select.options)
        .filter((option) => option.value !== "")
        .map(directoryRecord)
        .sort((left, right) => (
          left.directoryOrder - right.directoryOrder
          || left.directoryName.localeCompare(right.directoryName, "zh-CN")
          || (left.institution === "其他机构" ? 1 : 0)
          - (right.institution === "其他机构" ? 1 : 0)
          || left.institution.localeCompare(right.institution, "zh-CN")
          || left.currency.localeCompare(right.currency)
          || left.option.textContent.localeCompare(right.option.textContent, "zh-CN")
          || left.index - right.index
        ));
    }

    function directoryToggle({ label, count, expanded, className, dataset, onToggle }) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = `frequent-picker-directory-toggle ${className}`;
      button.setAttribute("aria-expanded", String(expanded));
      Object.assign(button.dataset, dataset);
      const marker = document.createElement("span");
      marker.className = "frequent-picker-directory-marker";
      marker.textContent = expanded ? "▾" : "▸";
      const name = document.createElement("span");
      name.textContent = label;
      const total = document.createElement("small");
      total.textContent = String(count);
      button.append(marker, name, total);
      button.addEventListener("click", (event) => {
        event.stopPropagation();
        onToggle();
      });
      return button;
    }

    function appendAccountDirectories(records) {
      const heading = document.createElement("p");
      heading.className = "frequent-picker-group-label frequent-picker-all-label";
      heading.textContent = "全部账户";
      menu.append(heading);

      const directories = new Map();
      for (const record of records) {
        if (!directories.has(record.directoryKey)) {
          directories.set(record.directoryKey, {
            key: record.directoryKey,
            name: record.directoryName,
            records: [],
          });
        }
        directories.get(record.directoryKey).records.push(record);
      }

      for (const directory of directories.values()) {
        const directoryExpanded = expandedDirectories.has(directory.key);
        menu.append(directoryToggle({
          label: directory.name,
          count: directory.records.length,
          expanded: directoryExpanded,
          className: "is-directory",
          dataset: { directoryKey: directory.key },
          onToggle: () => {
            if (directoryExpanded) expandedDirectories.delete(directory.key);
            else expandedDirectories.add(directory.key);
            renderMenu();
            menu.querySelector(
              `[data-directory-key="${CSS.escape(directory.key)}"]`,
            )?.focus();
          },
        }));
        if (!directoryExpanded) continue;

        const institutions = new Map();
        for (const record of directory.records) {
          if (!institutions.has(record.institution)) institutions.set(record.institution, []);
          institutions.get(record.institution).push(record);
        }
        for (const [institution, institutionRecords] of institutions) {
          const key = institutionKey(directory.key, institution);
          const institutionExpanded = expandedInstitutions.has(key);
          menu.append(directoryToggle({
            label: institution,
            count: institutionRecords.length,
            expanded: institutionExpanded,
            className: "is-institution",
            dataset: { institutionKey: key },
            onToggle: () => {
              if (institutionExpanded) expandedInstitutions.delete(key);
              else expandedInstitutions.add(key);
              renderMenu();
              menu.querySelector(
                `[data-institution-key="${CSS.escape(key)}"]`,
              )?.focus();
            },
          }));
          if (!institutionExpanded) continue;
          for (const record of institutionRecords) {
            menu.append(optionButton(record.option, choose, "is-directory-account"));
          }
        }
      }
    }

    function expandSelectedDirectory(commonValues) {
      const selected = selectedOption();
      if (!selected || !selected.value || commonValues.has(selected.value)) return;
      const record = directoryRecord(selected, 0);
      expandedDirectories.add(record.directoryKey);
      expandedInstitutions.add(
        institutionKey(record.directoryKey, record.institution),
      );
    }

    function renderMenu() {
      menu.replaceChildren();
      const allOptions = Array.from(select.options);
      const emptyOption = allOptions.find((option) => option.value === "");
      const records = allOptions.filter((option) => option.value !== "");
      wrapper.classList.toggle(
        "is-ranked-scroll",
        configuration.displayMode === "ranked-scroll",
      );
      wrapper.classList.toggle(
        "is-account-directory",
        configuration.displayMode === "account-directory",
      );
      if (configuration.displayMode === "ranked-scroll") {
        menu.style.setProperty("--picker-visible-rows", configuration.visibleRows);
        if (emptyOption && !emptyOption.disabled) {
          menu.append(optionButton(emptyOption, choose));
        }
        for (const item of rankedRecords(false)) {
          menu.append(optionButton(item.option, choose));
        }
        return;
      }
      menu.style.removeProperty("--picker-visible-rows");
      const common = rankedRecords()
        .slice(0, configuration.commonLimit)
        .map((item) => item.option);
      const commonValues = new Set(common.map((option) => option.value));

      if (configuration.displayMode === "account-directory") {
        if (emptyOption && !emptyOption.disabled) {
          menu.append(optionButton(emptyOption, choose));
        }
        appendGroup(configuration.commonLabel, common);
        appendAccountDirectories(directoryRecords());
        return;
      }

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
      if (configuration.displayMode === "account-directory") {
        const commonValues = new Set(
          rankedRecords().slice(0, configuration.commonLimit)
            .map((item) => item.option.value),
        );
        expandSelectedDirectory(commonValues);
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
