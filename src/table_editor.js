function clone(value) {
  return JSON.parse(JSON.stringify(value));
}

function emptyDraft() {
  return {
    rows: [],
    columns: [],
  };
}

function render({ model, el }) {
  el.classList.add("calc-table-editor");

  function getDraft() {
    const value = model.get("draft");
    return value ? clone(value) : emptyDraft();
  }

  function saveDraft(draft) {
    model.set("draft", draft);
    model.save_changes();
  }

  function errorsFor(row, column) {
    return (model.get("errors") || []).filter(
      (error) =>
        error.row === row &&
        error.column === column
    );
  }

  function makeCellInput(value, row, column, onChange) {
    const input = document.createElement("input");

    input.className = "cte-cell-input";
    input.type = "text";
    input.value = value ?? "";
    input.dataset.row = row;
    input.dataset.column = column;

    const errors = errorsFor(row, column);

    if (errors.length) {
      input.classList.add("cte-invalid");
      input.title = errors
        .map((error) => error.message)
        .join("\n");
    }

    input.addEventListener("change", () => {
      onChange(input.value);
    });

    return input;
  }

  function draw() {
    el.replaceChildren();

    const schema = model.get("schema") || [];
    const baseRows = model.get("rows") || [];
    const types = model.get("types") || [];
    const draft = getDraft();
    const disabled = model.get("disabled");

    // -------------------------------------------------------
    // TOOLBAR
    // -------------------------------------------------------

    const toolbar = document.createElement("div");
    toolbar.className = "cte-toolbar";

    const addRow = document.createElement("button");
    addRow.type = "button";
    addRow.textContent = "+ Row";
    addRow.disabled = disabled;

    addRow.addEventListener("click", () => {
      const next = getDraft();

      next.rows.push(
        schema.map(() => "")
      );

      // Every extended column needs one value for the new row.
      for (const column of next.columns) {
        column.values.push("");
      }

      saveDraft(next);
    });

    toolbar.appendChild(addRow);

    // -------------------------------------------------------
    // NEW COLUMN CONTROLS
    // -------------------------------------------------------

    const nameInput = document.createElement("input");
    nameInput.type = "text";
    nameInput.className = "cte-column-name";
    nameInput.placeholder = "column name";
    nameInput.disabled = disabled;

    const typeSelect = document.createElement("select");
    typeSelect.className = "cte-type-select";
    typeSelect.disabled = disabled;

    for (const type of types) {
      const option = document.createElement("option");
      option.value = type.key;
      option.textContent = type.label;
      typeSelect.appendChild(option);
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

      const next = getDraft();

      const names = [
        ...schema.map((column) => column.name),
        ...next.columns.map((column) => column.name),
      ].map((value) => value.toLowerCase());

      if (names.includes(name.toLowerCase())) {
        nameInput.setCustomValidity(
          `Column "${name}" already exists.`
        );
        nameInput.reportValidity();
        return;
      }

      nameInput.setCustomValidity("");

      next.columns.push({
        name,
        type: typeSelect.value,
        values: Array(
          baseRows.length + next.rows.length
        ).fill(""),
      });

      saveDraft(next);

      nameInput.value = "";
    });

    toolbar.appendChild(nameInput);
    toolbar.appendChild(typeSelect);
    toolbar.appendChild(addColumn);

    el.appendChild(toolbar);

    // -------------------------------------------------------
    // TABLE
    // -------------------------------------------------------

    const viewport = document.createElement("div");
    viewport.className = "cte-viewport";

    const table = document.createElement("table");
    table.className = "cte-grid";

    const thead = document.createElement("thead");
    const header = document.createElement("tr");

    // Existing columns.
    for (const column of schema) {
      const th = document.createElement("th");

      const name = document.createElement("div");
      name.className = "cte-header-name";
      name.textContent = column.name;

      const type = document.createElement("div");
      type.className = "cte-header-type";
      type.textContent = column.type;

      th.append(name, type);
      header.appendChild(th);
    }

    // New columns.
    draft.columns.forEach((column, draftColumnIndex) => {
      const th = document.createElement("th");
      th.className = "cte-new-column";

      const top = document.createElement("div");
      top.className = "cte-new-column-header";

      const name = document.createElement("span");
      name.className = "cte-header-name";
      name.textContent = column.name;

      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "cte-remove";
      remove.textContent = "×";
      remove.title = `Remove ${column.name}`;
      remove.disabled = disabled;

      remove.addEventListener("click", () => {
        const next = getDraft();
        next.columns.splice(draftColumnIndex, 1);
        saveDraft(next);
      });

      top.append(name, remove);

      const select = document.createElement("select");
      select.className = "cte-inline-type";
      select.disabled = disabled;

      for (const type of types) {
        const option = document.createElement("option");
        option.value = type.key;
        option.textContent = type.label;
        option.selected = type.key === column.type;
        select.appendChild(option);
      }

      select.addEventListener("change", () => {
        const next = getDraft();
        next.columns[draftColumnIndex].type = select.value;
        saveDraft(next);
      });

      th.append(top, select);
      header.appendChild(th);
    });

    // Row action column.
    const actionHeader = document.createElement("th");
    actionHeader.className = "cte-actions-column";
    header.appendChild(actionHeader);

    thead.appendChild(header);
    table.appendChild(thead);

    // -------------------------------------------------------
    // BODY
    // -------------------------------------------------------

    const tbody = document.createElement("tbody");

    const totalRows =
      baseRows.length +
      draft.rows.length;

    for (let rowIndex = 0; rowIndex < totalRows; rowIndex++) {
      const tr = document.createElement("tr");

      const isBaseRow =
        rowIndex < baseRows.length;

      // Existing columns.
      schema.forEach((column, columnIndex) => {
        const td = document.createElement("td");

        if (isBaseRow) {
          td.className = "cte-readonly";
          td.textContent =
            baseRows[rowIndex]?.[columnIndex] ?? "";
        } else {
          const draftRowIndex =
            rowIndex - baseRows.length;

          const value =
            draft.rows[draftRowIndex]?.[columnIndex] ?? "";

          td.appendChild(
            makeCellInput(
              value,
              rowIndex,
              columnIndex,
              (nextValue) => {
                const next = getDraft();
                next.rows[draftRowIndex][columnIndex] =
                  nextValue;
                saveDraft(next);
              }
            )
          );
        }

        tr.appendChild(td);
      });

      // Added columns are editable for every row.
      draft.columns.forEach(
        (column, draftColumnIndex) => {
          const outputColumn =
            schema.length + draftColumnIndex;

          const td = document.createElement("td");

          td.appendChild(
            makeCellInput(
              column.values[rowIndex] ?? "",
              rowIndex,
              outputColumn,
              (nextValue) => {
                const next = getDraft();

                next.columns[
                  draftColumnIndex
                ].values[rowIndex] = nextValue;

                saveDraft(next);
              }
            )
          );

          tr.appendChild(td);
        }
      );

      // Remove appended row.
      const action = document.createElement("td");
      action.className = "cte-row-action";

      if (!isBaseRow) {
        const draftRowIndex =
          rowIndex - baseRows.length;

        const remove = document.createElement("button");

        remove.type = "button";
        remove.className = "cte-remove";
        remove.textContent = "×";
        remove.title = "Remove appended row";
        remove.disabled = disabled;

        remove.addEventListener("click", () => {
          const next = getDraft();

          next.rows.splice(
            draftRowIndex,
            1
          );

          // The new columns cover both original and appended rows.
          // Remove the corresponding cell too.
          for (const column of next.columns) {
            column.values.splice(rowIndex, 1);
          }

          saveDraft(next);
        });

        action.appendChild(remove);
      }

      tr.appendChild(action);
      tbody.appendChild(tr);
    }

    table.appendChild(tbody);
    viewport.appendChild(table);
    el.appendChild(viewport);

    // -------------------------------------------------------
    // ERRORS
    // -------------------------------------------------------

    const errors = model.get("errors") || [];

    if (errors.length) {
      const panel = document.createElement("div");
      panel.className = "cte-errors";

      const title = document.createElement("strong");
      title.textContent =
        `${errors.length} invalid cell${errors.length === 1 ? "" : "s"}`;

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

  model.on("change:draft", draw);
  model.on("change:errors", draw);
  model.on("change:schema", draw);
  model.on("change:rows", draw);
  model.on("change:types", draw);
  model.on("change:disabled", draw);
}

export default { render };
