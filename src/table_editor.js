function clone(value) {
  return JSON.parse(JSON.stringify(value));
}

function emptyState() {
  return {
    schema: [],
    rows: [],
  };
}

function normalizeState(value) {
  const state = value ? clone(value) : emptyState();
  state.schema = Array.isArray(state.schema) ? state.schema : [];
  state.rows = Array.isArray(state.rows) ? state.rows : [];

  for (const column of state.schema) {
    column.name = column.name ?? "";
    column.type = column.type ?? "text";
    column.formula = column.formula ?? "";
  }

  for (const row of state.rows) {
    while (row.length < state.schema.length) row.push("");
    if (row.length > state.schema.length) row.splice(state.schema.length);
  }

  return state;
}

function render({ model, el }) {
  el.classList.add("calc-table-editor");
  let page = 0;

  function getState() {
    return normalizeState(model.get("state"));
  }

  function saveState(state) {
    model.set("state", normalizeState(state));
    model.save_changes();
  }

  function allErrors() {
    return model.get("errors") || [];
  }

  function errorsFor(row, column) {
    return allErrors().filter(
      (error) => error.row === row && error.column === column,
    );
  }

  function markInvalid(control, errors) {
    if (!errors.length) return;
    control.classList.add("cte-invalid");
    control.title = errors.map((error) => error.message).join("\n");
  }

  function makeCellInput(value, row, column, onChange) {
    const input = document.createElement("input");
    input.className = "cte-cell-input";
    input.type = "text";
    input.value = value ?? "";
    input.dataset.row = row;
    input.dataset.column = column;
    input.setAttribute("aria-label", `Row ${row + 1}, column ${column + 1}`);
    markInvalid(input, errorsFor(row, column));

    input.addEventListener("change", () => onChange(input.value));
    return input;
  }

  function typeSelect(types, value, onChange) {
    const select = document.createElement("select");
    select.className = "cte-inline-type";

    for (const type of types) {
      const option = document.createElement("option");
      option.value = type.key;
      option.textContent = type.label;
      option.selected = type.key === value;
      select.appendChild(option);
    }

    select.addEventListener("change", () => onChange(select.value));
    return select;
  }

  function drawPagination(parent, totalRows, pageSize) {
    const pageCount = Math.max(1, Math.ceil(totalRows / pageSize));
    page = Math.min(page, pageCount - 1);

    const nav = document.createElement("div");
    nav.className = "cte-pagination";

    const previous = document.createElement("button");
    previous.type = "button";
    previous.textContent = "Previous";
    previous.disabled = page === 0;
    previous.addEventListener("click", () => {
      page -= 1;
      draw();
    });

    const status = document.createElement("span");
    status.textContent = totalRows
      ? `Rows ${page * pageSize + 1}-${Math.min((page + 1) * pageSize, totalRows)} of ${totalRows}`
      : "0 rows";

    const next = document.createElement("button");
    next.type = "button";
    next.textContent = "Next";
    next.disabled = page >= pageCount - 1;
    next.addEventListener("click", () => {
      page += 1;
      draw();
    });

    nav.append(previous, status, next);
    parent.appendChild(nav);
  }

  function draw() {
    el.replaceChildren();

    const state = getState();
    const schema = state.schema;
    const rows = state.rows;
    const displayRows = model.get("display_rows") || rows;
    const types = model.get("types") || [];
    const disabled = Boolean(model.get("disabled"));
    const pageSize = Math.max(1, Number(model.get("page_size") || 25));

    const toolbar = document.createElement("div");
    toolbar.className = "cte-toolbar";

    const addRow = document.createElement("button");
    addRow.type = "button";
    addRow.textContent = "+ Row";
    addRow.disabled = disabled || schema.length === 0;
    addRow.addEventListener("click", () => {
      const next = getState();
      next.rows.push(next.schema.map(() => ""));
      page = Math.floor((next.rows.length - 1) / pageSize);
      saveState(next);
    });

    const nameInput = document.createElement("input");
    nameInput.type = "text";
    nameInput.className = "cte-column-name";
    nameInput.placeholder = "column name";
    nameInput.disabled = disabled;
    nameInput.setAttribute("aria-label", "New column name");

    const newType = document.createElement("select");
    newType.className = "cte-type-select";
    newType.disabled = disabled;
    newType.setAttribute("aria-label", "New column type");
    for (const type of types) {
      const option = document.createElement("option");
      option.value = type.key;
      option.textContent = type.label;
      newType.appendChild(option);
    }

    const addColumn = document.createElement("button");
    addColumn.type = "button";
    addColumn.textContent = "+ Column";
    addColumn.disabled = disabled;
    addColumn.addEventListener("click", () => {
      const name = nameInput.value.trim();
      if (!name) {
        nameInput.focus();
        return;
      }

      const next = getState();
      const names = next.schema.map((column) => column.name.toLowerCase());
      if (names.includes(name.toLowerCase())) {
        nameInput.setCustomValidity(`Column "${name}" already exists.`);
        nameInput.reportValidity();
        return;
      }

      nameInput.setCustomValidity("");
      next.schema.push({ name, type: newType.value, formula: "" });
      for (const row of next.rows) row.push("");
      saveState(next);
      nameInput.value = "";
    });

    toolbar.append(addRow, nameInput, newType, addColumn);
    el.appendChild(toolbar);

    const formulaHelp = document.createElement("div");
    formulaHelp.className = "cte-formula-help";
    formulaHelp.textContent =
      "Column formulas can use date::DAYNAME (normalized to [date]::DAYNAME) or [end_time] - [start_time].";
    el.appendChild(formulaHelp);

    if (schema.length === 0) {
      const empty = document.createElement("div");
      empty.className = "cte-empty";
      empty.textContent = "Add a column to start building the table.";
      el.appendChild(empty);
      return;
    }

    const viewport = document.createElement("div");
    viewport.className = "cte-viewport";
    const table = document.createElement("table");
    table.className = "cte-grid";

    const thead = document.createElement("thead");
    const header = document.createElement("tr");

    schema.forEach((column, columnIndex) => {
      const th = document.createElement("th");
      th.className = column.formula.trim() ? "cte-computed-column" : "";

      const top = document.createElement("div");
      top.className = "cte-column-header";

      const name = document.createElement("input");
      name.className = "cte-header-name-input";
      name.type = "text";
      name.value = column.name;
      name.disabled = disabled;
      name.setAttribute("aria-label", `Column ${columnIndex + 1} name`);
      name.addEventListener("change", () => {
        const nextName = name.value.trim();
        const next = getState();
        const duplicate = next.schema.some(
          (item, index) =>
            index !== columnIndex && item.name.toLowerCase() === nextName.toLowerCase(),
        );
        if (!nextName || duplicate) {
          name.setCustomValidity(
            !nextName ? "Column name cannot be empty." : `Column "${nextName}" already exists.`,
          );
          name.reportValidity();
          return;
        }
        name.setCustomValidity("");
        next.schema[columnIndex].name = nextName;
        saveState(next);
      });

      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "cte-remove";
      remove.textContent = "×";
      remove.title = `Remove ${column.name}`;
      remove.disabled = disabled;
      remove.addEventListener("click", () => {
        const next = getState();
        next.schema.splice(columnIndex, 1);
        for (const row of next.rows) row.splice(columnIndex, 1);
        saveState(next);
      });

      top.append(name, remove);

      const select = typeSelect(types, column.type, (value) => {
        const next = getState();
        next.schema[columnIndex].type = value;
        saveState(next);
      });
      select.disabled = disabled;
      select.setAttribute("aria-label", `${column.name} type`);

      const formula = document.createElement("input");
      formula.className = "cte-formula-input";
      formula.type = "text";
      formula.value = column.formula || "";
      formula.placeholder = "formula (optional)";
      formula.disabled = disabled;
      formula.setAttribute("aria-label", `${column.name} formula`);
      markInvalid(formula, errorsFor(-1, columnIndex));
      formula.addEventListener("change", () => {
        const next = getState();
        next.schema[columnIndex].formula = formula.value.trim();
        saveState(next);
      });

      th.append(top, select, formula);
      header.appendChild(th);
    });

    const actionHeader = document.createElement("th");
    actionHeader.className = "cte-actions-column";
    header.appendChild(actionHeader);
    thead.appendChild(header);
    table.appendChild(thead);

    const tbody = document.createElement("tbody");
    const pageCount = Math.max(1, Math.ceil(rows.length / pageSize));
    page = Math.min(page, pageCount - 1);
    const start = page * pageSize;
    const end = Math.min(start + pageSize, rows.length);

    for (let rowIndex = start; rowIndex < end; rowIndex++) {
      const tr = document.createElement("tr");

      schema.forEach((column, columnIndex) => {
        const td = document.createElement("td");
        if (column.formula.trim()) {
          td.className = "cte-formula-cell";
          const value = displayRows[rowIndex]?.[columnIndex] ?? "";
          td.textContent = value;
          const cellErrors = errorsFor(rowIndex, columnIndex);
          if (cellErrors.length) {
            td.classList.add("cte-invalid");
            td.title = cellErrors.map((error) => error.message).join("\n");
          }
        } else {
          td.appendChild(
            makeCellInput(
              rows[rowIndex]?.[columnIndex] ?? "",
              rowIndex,
              columnIndex,
              (nextValue) => {
                const next = getState();
                next.rows[rowIndex][columnIndex] = nextValue;
                saveState(next);
              },
            ),
          );
        }
        tr.appendChild(td);
      });

      const action = document.createElement("td");
      action.className = "cte-row-action";
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "cte-remove";
      remove.textContent = "×";
      remove.title = `Remove row ${rowIndex + 1}`;
      remove.disabled = disabled;
      remove.addEventListener("click", () => {
        const next = getState();
        next.rows.splice(rowIndex, 1);
        saveState(next);
      });
      action.appendChild(remove);
      tr.appendChild(action);
      tbody.appendChild(tr);
    }

    table.appendChild(tbody);
    viewport.appendChild(table);
    el.appendChild(viewport);
    drawPagination(el, rows.length, pageSize);

    const errors = allErrors();
    if (errors.length) {
      const panel = document.createElement("div");
      panel.className = "cte-errors";
      const title = document.createElement("strong");
      title.textContent = `${errors.length} table error${errors.length === 1 ? "" : "s"}`;
      panel.appendChild(title);

      const list = document.createElement("ul");
      for (const error of errors) {
        const item = document.createElement("li");
        item.textContent = error.message;
        list.appendChild(item);
      }
      panel.appendChild(list);
      el.appendChild(panel);
    }
  }

  draw();
  model.on("change:state", draw);
  model.on("change:display_rows", draw);
  model.on("change:errors", draw);
  model.on("change:types", draw);
  model.on("change:disabled", draw);
  model.on("change:page_size", draw);
}

export default { render };
