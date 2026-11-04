"use strict";

const page = document.body.dataset.page;
const role = document.body.dataset.role;
const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || "";
const responseBox = document.getElementById("response");
const weekdayNames = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

class ApiFailure extends Error {
  constructor(status, body) {
    super(body?.message || `Request failed (${status}).`);
    this.status = status;
    this.code = body?.error || "request_failed";
  }
}

async function api(path, options = {}) {
  const method = (options.method || "GET").toUpperCase();
  const headers = new Headers(options.headers || {});
  if (options.body !== undefined) headers.set("Content-Type", "application/json");
  if (!["GET", "HEAD", "OPTIONS"].includes(method)) headers.set("X-CSRF-Token", csrfToken);
  const response = await fetch(path, { ...options, method, headers, credentials: "same-origin" });
  const contentType = response.headers.get("content-type") || "";
  const body = contentType.includes("application/json") ? await response.json() : null;
  if (!response.ok) throw new ApiFailure(response.status, body);
  return body;
}

function jsonOptions(method, data) {
  return { method, body: JSON.stringify(data) };
}

function showMessage(message, error = false) {
  if (!responseBox) return;
  responseBox.textContent = message;
  responseBox.classList.toggle("error", error);
  responseBox.hidden = false;
  responseBox.scrollIntoView({ block: "nearest", behavior: "smooth" });
}

function reportFailure(error) {
  showMessage(error instanceof Error ? error.message : "An unexpected error occurred.", true);
}

function element(tag, value = "", className = "") {
  const node = document.createElement(tag);
  if (value !== undefined && value !== null) node.textContent = String(value);
  if (className) node.className = className;
  return node;
}

function button(label, action, className = "") {
  const control = element("button", label, className);
  control.type = "button";
  control.addEventListener("click", async () => {
    control.disabled = true;
    try { await action(); } catch (error) { reportFailure(error); }
    finally { control.disabled = false; }
  });
  return control;
}

function tableCell(row, value) {
  const cell = element("td");
  if (value instanceof Node) cell.append(value);
  else cell.textContent = value === null || value === undefined || value === "" ? "—" : String(value);
  row.append(cell);
  return cell;
}

function renderRows(body, records, columns, render) {
  if (!body) return;
  body.replaceChildren();
  if (!records.length) {
    const row = element("tr");
    tableCell(row, "No records found.").colSpan = columns;
    body.append(row);
    return;
  }
  for (const record of records) body.append(render(record));
}

function pagedItems(body) {
  return Array.isArray(body?.items) ? body.items : [];
}

function option(label, value = "") {
  const item = element("option", label);
  item.value = value === null ? "" : String(value);
  return item;
}

function populate(select, items, label, blankLabel = "Choose…", selectedValue = undefined) {
  if (!select) return;
  const prior = select.multiple
    ? new Set(selectedValue === undefined ? [...select.selectedOptions].map((item) => item.value) : [String(selectedValue ?? "")])
    : (selectedValue === undefined ? select.value : String(selectedValue ?? ""));
  select.replaceChildren();
  if (!select.multiple) select.append(option(blankLabel, ""));
  for (const item of items) {
    const choice = option(label(item), item.id);
    choice.disabled = item.active === false;
    if (select.multiple && prior.has(choice.value)) choice.selected = true;
    select.append(choice);
  }
  if (!select.multiple && [...select.options].some((item) => item.value === prior)) select.value = prior;
}

function selectedNumbers(select) {
  return [...select.selectedOptions].map((item) => Number(item.value));
}

function formValues(form) {
  const values = {};
  for (const [key, value] of new FormData(form).entries()) values[key] = value;
  return values;
}

function localToday() {
  return new Date().toISOString().slice(0, 10);
}

function displayMoney(cents) {
  return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" }).format(Number(cents || 0) / 100);
}

function statusPill(status) {
  return element("span", status.replaceAll("_", " "), `status ${status}`);
}

async function loadPatients(search = "") {
  const query = new URLSearchParams({ limit: "100", offset: "0" });
  if (search) query.set("search", search);
  const [result, dentists, services, rooms] = await Promise.all([
    api(`/api/patients?${query}`), api("/api/dentists?active=true"), api("/api/services?active=true"), api("/api/rooms?active=true"),
  ]);
  return { patients: pagedItems(result), dentists: pagedItems(dentists), services: pagedItems(services), rooms: pagedItems(rooms) };
}

async function setupHome() {
  const today = localToday();
  const [daily, reminderData] = await Promise.all([
    api(`/api/reports/daily?date=${encodeURIComponent(today)}`),
    api("/api/reminders?status=pending&limit=1"),
  ]);
  document.getElementById("metric-total").textContent = String(daily.agenda.length);
  document.getElementById("metric-queue").textContent = String(daily.queue.length);
  document.getElementById("metric-confirmed").textContent = String(daily.counts.confirmed);
  document.getElementById("metric-reminders").textContent = String(reminderData.total);
  renderRows(document.getElementById("agenda-records"), daily.agenda, 6, (visit) => {
    const row = element("tr");
    tableCell(row, visit.legacy_imported ? `Date only · ${visit.start_at.slice(0, 10)}` : `${visit.start_at.slice(11)}–${visit.end_at.slice(11)}`);
    tableCell(row, visit.patient_name); tableCell(row, visit.dentist_name);
    tableCell(row, visit.service_name || (visit.legacy_imported ? "Legacy date only" : "—"));
    tableCell(row, visit.room_name || (visit.legacy_imported ? "Needs rescheduling" : "—"));
    tableCell(row, statusPill(visit.status));
    return row;
  });
}

async function setupPatients() {
  const form = document.getElementById("patient-form");
  const search = document.getElementById("patient-search");
  const pageSize = 50;
  let offset = 0;
  async function refresh() {
    const query = new URLSearchParams({ limit: String(pageSize), offset: String(offset) });
    if (search.value.trim()) query.set("search", search.value.trim());
    const data = await api(`/api/patients?${query}`);
    const first = data.total === 0 ? 0 : offset + 1;
    const last = offset + pagedItems(data).length;
    document.getElementById("patient-result-count").textContent = `Showing ${first}–${last} of ${data.total} matching patients`;
    document.getElementById("patient-previous").disabled = offset === 0;
    document.getElementById("patient-next").disabled = offset + pageSize >= data.total;
    renderRows(document.getElementById("patient-records"), pagedItems(data), 6, (patient) => {
      const row = element("tr");
      tableCell(row, `${patient.first_name} ${patient.last_name}${patient.active ? "" : " (archived)"}`);
      tableCell(row, patient.patient_number); tableCell(row, patient.email);
      const address = patient.address ? [patient.address.street, patient.address.number, patient.address.city, patient.address.region].filter(Boolean).join(", ") : "";
      tableCell(row, address); tableCell(row, patient.reminder_consent ? "Yes" : "No");
      const actions = element("div", "", "action-group");
      if (form) {
        actions.append(button("Edit", () => {
          document.getElementById("patient-form-title").textContent = "Edit patient";
          for (const field of ["id", "first_name", "last_name", "email", "patient_number", "registered_on"]) form.elements[field].value = patient[field] ?? "";
          form.elements.reminder_consent.checked = patient.reminder_consent;
          const addressData = patient.address || {};
          for (const field of ["street", "number", "city", "region"]) form.elements[field].value = addressData[field] ?? "";
          form.scrollIntoView({ behavior: "smooth", block: "center" });
        }));
        if (patient.active) actions.append(button("Archive", async () => { await api(`/api/patients/${patient.id}`, jsonOptions("PATCH", { active: false })); await refresh(); showMessage("Patient archived."); }, "secondary"));
      }
      actions.append(button("Visit history", async () => {
        const history = await api(`/api/patient-history/${patient.id}`);
        const visits = history.appointments.items;
        const summary = history.appointments.total ? `${visits.map((visit) => `${visit.legacy_imported ? `Date only · ${visit.start_at.slice(0, 10)}` : visit.start_at} · ${visit.status}`).join("\n")}\nShowing ${visits.length} of ${history.appointments.total}.` : "No appointments on record.";
        window.alert(`${patient.first_name} ${patient.last_name}\n\n${summary}`);
      }, "secondary"));
      tableCell(row, actions);
      return row;
    });
  }
  if (form) {
    form.addEventListener("reset", () => { document.getElementById("patient-form-title").textContent = "Add patient"; });
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const raw = formValues(form);
      const address = { street: raw.street, number: raw.number || null, city: raw.city, region: raw.region };
      const hasAddress = Object.values(address).some((value) => value !== null && value !== "");
      const payload = {
        first_name: raw.first_name, last_name: raw.last_name, email: raw.email,
        patient_number: raw.patient_number || null, registered_on: raw.registered_on || null,
        address: hasAddress ? address : null, reminder_consent: form.elements.reminder_consent.checked,
      };
      const id = raw.id;
      if (id) { payload.active = true; await api(`/api/patients/${id}`, jsonOptions("PATCH", payload)); }
      else await api("/api/patients", jsonOptions("POST", payload));
      form.reset(); await refresh(); showMessage(id ? "Patient updated." : "Patient added.");
    });
  }
  document.getElementById("patient-previous").addEventListener("click", () => { offset = Math.max(0, offset - pageSize); refresh().catch(reportFailure); });
  document.getElementById("patient-next").addEventListener("click", () => { offset += pageSize; refresh().catch(reportFailure); });
  search.addEventListener("input", () => { offset = 0; refresh().catch(reportFailure); });
  await refresh();
}

async function setupDentists() {
  const form = document.getElementById("dentist-form");
  const qualificationForm = document.getElementById("qualification-form");
  async function refresh() {
    const [dentistData, serviceData] = await Promise.all([api("/api/dentists?limit=200"), api("/api/services?limit=200")]);
    const people = pagedItems(dentistData);
    const services = pagedItems(serviceData);
    const serviceMap = new Map(services.map((service) => [service.id, service.name]));
    renderRows(document.getElementById("dentist-records"), people, 5, (person) => {
      const row = element("tr");
      tableCell(row, `${person.first_name} ${person.last_name}`); tableCell(row, person.license_number); tableCell(row, person.active ? "Yes" : "No");
      const qualCell = element("td", "Loading…"); row.append(qualCell);
      api(`/api/dentists/${person.id}/services`).then((result) => { qualCell.textContent = result.service_ids.map((id) => serviceMap.get(id) || `Service ${id}`).join(", ") || "None"; }).catch(() => { qualCell.textContent = "Unavailable"; });
      const actions = element("div", "", "action-group");
      if (form) {
        actions.append(button("Edit", () => {
          document.getElementById("dentist-form-title").textContent = "Edit practitioner";
          for (const field of ["id", "first_name", "last_name", "license_number"]) form.elements[field].value = person[field] ?? "";
          form.scrollIntoView({ behavior: "smooth", block: "center" });
        }));
        if (person.active) actions.append(button("Archive", async () => { await api(`/api/dentists/${person.id}`, jsonOptions("PATCH", { active: false })); await refresh(); showMessage("Practitioner archived."); }, "secondary"));
      }
      tableCell(row, actions); return row;
    });
    if (qualificationForm) {
      populate(qualificationForm.elements.dentist_id, people.filter((item) => item.active), (item) => `${item.first_name} ${item.last_name}`, "Choose practitioner");
      populate(qualificationForm.elements.service_ids, services.filter((item) => item.active), (item) => item.name, "Choose services");
    }
  }
  if (form) {
    form.addEventListener("reset", () => { document.getElementById("dentist-form-title").textContent = "Add practitioner"; });
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const raw = formValues(form);
      const payload = { first_name: raw.first_name, last_name: raw.last_name, license_number: raw.license_number };
      const id = raw.id;
      if (id) await api(`/api/dentists/${id}`, jsonOptions("PATCH", payload));
      else await api("/api/dentists", jsonOptions("POST", payload));
      form.reset(); await refresh(); showMessage(id ? "Practitioner updated." : "Practitioner added.");
    });
  }
  if (qualificationForm) qualificationForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const dentistId = qualificationForm.elements.dentist_id.value;
    await api(`/api/dentists/${dentistId}/services`, jsonOptions("PUT", { service_ids: selectedNumbers(qualificationForm.elements.service_ids) }));
    await refresh(); showMessage("Practitioner qualifications saved.");
  });
  await refresh();
}

async function setupAppointments() {
  const form = document.getElementById("appointment-form");
  const filter = document.getElementById("appointment-filter-date");
  const [people, dentists, services, rooms] = await Promise.all([
    api("/api/patients?active=true&limit=200"), api("/api/dentists?active=true&limit=200"), api("/api/services?active=true&limit=200"), api("/api/rooms?active=true&limit=200"),
  ]);
  const patients = pagedItems(people), practitioners = pagedItems(dentists), serviceItems = pagedItems(services), roomItems = pagedItems(rooms);
  populate(form.elements.patient_id, patients, (item) => `${item.first_name} ${item.last_name}`, "Choose patient");
  populate(form.elements.dentist_id, practitioners, (item) => `${item.first_name} ${item.last_name}`, "Choose practitioner");
  populate(form.elements.service_id, serviceItems, (item) => `${item.name} · ${item.duration_minutes} min · ${displayMoney(item.price_cents)}`, "Choose service");
  populate(form.elements.room_id, roomItems, (item) => item.name, "Choose room");
  form.elements.date.value = localToday();
  filter.value = localToday();

  async function loadSlots() {
    const { date: day, dentist_id: dentistId, service_id: serviceId, room_id: roomId, patient_id: patientId } = form.elements;
    if (!day.value || !dentistId.value || !serviceId.value || !roomId.value) {
      populate(form.elements.start_at, [], () => "", "Choose a date and all resources first");
      return;
    }
    const query = new URLSearchParams({ date: day.value, dentist_id: dentistId.value, service_id: serviceId.value, room_id: roomId.value });
    if (patientId.value) query.set("patient_id", patientId.value);
    const availability = await api(`/api/availability?${query}`);
    const available = availability.slots;
    form.elements.start_at.replaceChildren(option(available.length ? "Choose an available start time" : "No available times", ""));
    for (const slot of available) form.elements.start_at.append(option(`${slot.start_at.slice(11)}–${slot.end_at.slice(11)}`, slot.start_at));
  }

  async function refresh() {
    const query = new URLSearchParams({ limit: "200" });
    if (filter.value) { query.set("from", filter.value); query.set("to", filter.value); }
    const result = await api(`/api/appointments?${query}`);
    renderRows(document.getElementById("appointment-records"), pagedItems(result), 7, (visit) => {
      const row = element("tr");
      tableCell(row, visit.legacy_imported ? `Date only · ${visit.start_at.slice(0, 10)}` : `${visit.start_at.replace("T", " ")}–${visit.end_at.slice(11)}`);
      tableCell(row, visit.patient_name); tableCell(row, visit.dentist_name);
      tableCell(row, `${visit.service_name || (visit.legacy_imported ? "Legacy date only" : "—")} / ${visit.room_name || "—"}`);
      const status = element("div"); status.append(statusPill(visit.status));
      if (visit.legacy_imported) status.append(element("p", "Imported date only. Placeholder time is not a confirmed appointment.", "small-note"));
      tableCell(row, status);
      tableCell(row, `${visit.duration_minutes} min · ${displayMoney(visit.price_cents)}${visit.legacy_imported ? " · needs rescheduling" : ""}`);
      const actions = element("div", "", "action-group");
      if (["booked", "confirmed"].includes(visit.status)) actions.append(button("Reschedule", async () => {
        document.getElementById("appointment-form-title").textContent = visit.legacy_imported ? "Schedule imported visit" : "Reschedule appointment";
        form.elements.id.value = visit.id; form.elements.version.value = visit.version;
        form.elements.patient_id.value = visit.patient_id; form.elements.dentist_id.value = visit.dentist_id;
        form.elements.service_id.value = visit.service_id || ""; form.elements.room_id.value = visit.room_id || "";
        form.elements.date.value = visit.start_at.slice(0, 10); form.elements.reason.value = visit.reason;
        await loadSlots(); form.scrollIntoView({ behavior: "smooth", block: "center" });
      }));
      const transitions = { booked: ["confirmed", "cancelled", "no_show"], confirmed: ["checked_in", "cancelled", "no_show"], checked_in: ["completed", "cancelled"] };
      for (const next of transitions[visit.status] || []) {
        if (next === "completed" && role !== "ADMIN") continue;
        if (visit.legacy_imported && ["confirmed", "checked_in", "completed", "no_show"].includes(next)) continue;
        actions.append(button(next.replaceAll("_", " "), async () => {
          const reason = next === "cancelled" ? window.prompt("Reason for cancellation:") : "";
          if (next === "cancelled" && !reason) return;
          await api(`/api/appointments/${visit.id}/transitions`, jsonOptions("POST", { status: next, version: visit.version, ...(reason ? { reason } : {}) }));
          await refresh(); showMessage(`Appointment ${next.replaceAll("_", " ")}.`);
        }, "secondary"));
      }
      tableCell(row, actions); return row;
    });
  }

  for (const field of [form.elements.date, form.elements.dentist_id, form.elements.service_id, form.elements.room_id, form.elements.patient_id]) field.addEventListener("change", () => loadSlots().catch(reportFailure));
  form.addEventListener("reset", () => { document.getElementById("appointment-form-title").textContent = "Book an appointment"; setTimeout(() => { form.elements.date.value = localToday(); loadSlots().catch(reportFailure); }, 0); });
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const raw = formValues(form);
    const payload = { patient_id: Number(raw.patient_id), dentist_id: Number(raw.dentist_id), service_id: Number(raw.service_id), room_id: Number(raw.room_id), start_at: raw.start_at, reason: raw.reason };
    const id = raw.id;
    if (id) await api(`/api/appointments/${id}`, jsonOptions("PATCH", { ...payload, version: Number(raw.version) }));
    else await api("/api/appointments", jsonOptions("POST", payload));
    form.reset(); await refresh(); showMessage(id ? "Appointment rescheduled." : "Appointment booked.");
  });
  filter.addEventListener("change", () => refresh().catch(reportFailure));
  await refresh();
}

async function setupCatalog() {
  async function refresh() {
    const [serviceData, roomData] = await Promise.all([api("/api/services?limit=200"), api("/api/rooms?limit=200")]);
    const services = pagedItems(serviceData), rooms = pagedItems(roomData);
    const serviceList = document.getElementById("service-records");
    serviceList.replaceChildren();
    if (!services.length) serviceList.append(element("p", "No services have been added."));
    for (const service of services) {
      const row = element("div", "", "record-row"); const detail = element("div");
      detail.append(element("strong", service.name), element("p", `${service.duration_minutes} min · ${displayMoney(service.price_cents)}${service.active ? "" : " · archived"}`));
      const actions = element("div", "", "action-group");
      actions.append(button("Edit", async () => {
        const name = window.prompt("Service name:", service.name); if (name === null) return;
        const duration = window.prompt("Duration in minutes:", String(service.duration_minutes)); if (duration === null) return;
        const price = window.prompt("Price in cents:", String(service.price_cents)); if (price === null) return;
        await api(`/api/services/${service.id}`, jsonOptions("PATCH", { name, duration_minutes: Number(duration), price_cents: Number(price) })); await refresh(); showMessage("Service updated.");
      }, "secondary"));
      if (service.active) actions.append(button("Archive", async () => { await api(`/api/services/${service.id}`, jsonOptions("PATCH", { active: false })); await refresh(); showMessage("Service archived."); }, "secondary"));
      row.append(detail, actions); serviceList.append(row);
    }
    const roomList = document.getElementById("room-records"); roomList.replaceChildren();
    if (!rooms.length) roomList.append(element("p", "No rooms have been added."));
    for (const room of rooms) {
      const row = element("div", "", "record-row"); const detail = element("div");
      detail.append(element("strong", room.name), element("p", room.active ? "Active" : "Archived"));
      const actions = element("div", "", "action-group");
      actions.append(button("Rename", async () => { const name = window.prompt("Room name:", room.name); if (name === null) return; await api(`/api/rooms/${room.id}`, jsonOptions("PATCH", { name })); await refresh(); showMessage("Room updated."); }, "secondary"));
      if (room.active) actions.append(button("Archive", async () => { await api(`/api/rooms/${room.id}`, jsonOptions("PATCH", { active: false })); await refresh(); showMessage("Room archived."); }, "secondary"));
      row.append(detail, actions); roomList.append(row);
    }
    const [dentists] = await Promise.all([api("/api/dentists?active=true&limit=200")]);
    populate(document.querySelector('#qualification-form [name="dentist_id"]'), pagedItems(dentists), (item) => `${item.first_name} ${item.last_name}`, "Choose practitioner");
    populate(document.querySelector('#qualification-form [name="service_ids"]'), services, (item) => item.name, "Choose services");
  }
  document.getElementById("service-form").addEventListener("submit", async (event) => {
    event.preventDefault(); const form = event.currentTarget; const raw = formValues(form);
    await api("/api/services", jsonOptions("POST", { name: raw.name, duration_minutes: Number(raw.duration_minutes), price_cents: Number(raw.price_cents) }));
    form.reset(); await refresh(); showMessage("Service added.");
  });
  document.getElementById("room-form").addEventListener("submit", async (event) => {
    event.preventDefault(); const form = event.currentTarget; const raw = formValues(form); await api("/api/rooms", jsonOptions("POST", { name: raw.name })); form.reset(); await refresh(); showMessage("Room added.");
  });
  document.getElementById("qualification-form").addEventListener("submit", async (event) => {
    event.preventDefault(); const form = event.currentTarget; const dentistId = form.elements.dentist_id.value;
    await api(`/api/dentists/${dentistId}/services`, jsonOptions("PUT", { service_ids: selectedNumbers(form.elements.service_ids) })); await refresh(); showMessage("Practitioner qualifications saved.");
  });
  await refresh();
}

async function setupSchedule() {
  const dentists = pagedItems(await api("/api/dentists?active=true&limit=200"));
  for (const select of document.querySelectorAll('#shift-form [name="dentist_id"], #time-off-form [name="dentist_id"]')) populate(select, dentists, (person) => `${person.first_name} ${person.last_name}`, "Choose practitioner");
  async function refresh() {
    const [shiftData, timeOffData] = await Promise.all([api("/api/shifts?limit=200"), api("/api/time-off?limit=200")]);
    const shifts = pagedItems(shiftData), timeOff = pagedItems(timeOffData);
    const shiftList = document.getElementById("shift-records"); shiftList.replaceChildren();
    const names = new Map(dentists.map((person) => [person.id, `${person.first_name} ${person.last_name}`]));
    if (!shifts.length) shiftList.append(element("p", "No weekly hours have been published."));
    for (const shift of shifts) {
      const row = element("div", "", "record-row"); const detail = element("div");
      detail.append(element("strong", names.get(shift.dentist_id) || `Practitioner ${shift.dentist_id}`), element("p", `${weekdayNames[shift.weekday]} · ${shift.start_time}–${shift.end_time}`));
      row.append(detail, button("Remove", async () => { await api(`/api/shifts/${shift.id}`, { method: "DELETE" }); await refresh(); showMessage("Weekly hours removed."); }, "secondary")); shiftList.append(row);
    }
    const timeList = document.getElementById("time-off-records"); timeList.replaceChildren();
    if (!timeOff.length) timeList.append(element("p", "No time off is recorded."));
    for (const leave of timeOff) {
      const row = element("div", "", "record-row"); const detail = element("div");
      detail.append(element("strong", names.get(leave.dentist_id) || `Practitioner ${leave.dentist_id}`), element("p", `${leave.start_at}–${leave.end_at} · ${leave.reason}`));
      row.append(detail, button("Remove", async () => { await api(`/api/time-off/${leave.id}`, { method: "DELETE" }); await refresh(); showMessage("Time off removed."); }, "secondary")); timeList.append(row);
    }
  }
  document.getElementById("shift-form").addEventListener("submit", async (event) => {
    event.preventDefault(); const form = event.currentTarget; const raw = formValues(form);
    await api("/api/shifts", jsonOptions("POST", { dentist_id: Number(raw.dentist_id), weekday: Number(raw.weekday), start_time: raw.start_time, end_time: raw.end_time }));
    form.reset(); await refresh(); showMessage("Weekly working hours added.");
  });
  document.getElementById("time-off-form").addEventListener("submit", async (event) => {
    event.preventDefault(); const form = event.currentTarget; const raw = formValues(form);
    await api("/api/time-off", jsonOptions("POST", { dentist_id: Number(raw.dentist_id), start_at: raw.start_at, end_at: raw.end_at, reason: raw.reason }));
    form.reset(); await refresh(); showMessage("Time off recorded.");
  });
  await refresh();
}

async function setupCare() {
  const [patientData, serviceData, appointmentData] = await Promise.all([api("/api/patients?active=true&limit=200"), api("/api/services?active=true&limit=200"), api("/api/appointments?limit=200")]);
  const patients = pagedItems(patientData), services = pagedItems(serviceData), appointments = pagedItems(appointmentData);
  populate(document.querySelector('#plan-form [name="patient_id"]'), patients, (item) => `${item.first_name} ${item.last_name}`, "Choose patient");
  populate(document.querySelector('#note-form [name="patient_id"]'), patients, (item) => `${item.first_name} ${item.last_name}`, "Choose patient");
  populate(document.querySelector('#note-form [name="appointment_id"]'), appointments, (visit) => `${visit.start_at} · ${visit.patient_name} · ${visit.status}`, "No appointment selected");
  populate(document.querySelector('#note-form [name="amends_note_id"]'), [], () => "", "This is a new note");
  populate(document.querySelector('#item-form [name="service_id"]'), services, (item) => `${item.name} · ${displayMoney(item.price_cents)}`, "Choose service");
  populate(document.querySelector('#item-schedule-form [name="appointment_id"]'), appointments.filter((visit) => ["booked", "confirmed"].includes(visit.status) && !visit.legacy_imported), (visit) => `${visit.start_at} · ${visit.patient_name} · ${visit.service_name}`, "Choose appointment");
  const notePatient = document.querySelector('#note-form [name="patient_id"]');
  async function refresh() {
    const [planData, noteData] = await Promise.all([api("/api/treatment-plans?limit=200"), api("/api/clinical-notes?limit=200")]);
    const plans = pagedItems(planData), notes = pagedItems(noteData);
    const planList = document.getElementById("plan-records"); planList.replaceChildren();
    if (!plans.length) planList.append(element("p", "No treatment plans have been created."));
    const draftPlans = plans.filter((plan) => plan.status === "draft");
    populate(document.querySelector('#item-form [name="plan_id"]'), draftPlans, (plan) => `Plan ${plan.id} · ${plan.title}`, "Choose a draft plan");
    const plannedItems = plans.flatMap((plan) => plan.items.filter((item) => plan.status === "active" && item.status === "planned").map((item) => ({ ...item, plan_title: plan.title })));
    populate(document.querySelector('#item-schedule-form [name="item_id"]'), plannedItems, (item) => `Plan ${item.plan_id} · ${item.sequence}. ${item.service_name_snapshot || `Service ${item.service_id}`}`, "Choose planned item");
    for (const plan of plans) {
      const row = element("article", "", "record-row plan-row"); const detail = element("div");
      detail.append(element("strong", `${plan.title} · patient ${plan.patient_id}`), statusPill(plan.status));
      for (const item of plan.items) detail.append(element("p", `${item.sequence}. ${item.service_name_snapshot || `Service ${item.service_id}`} · ${item.status}${item.tooth_reference ? ` · tooth ${item.tooth_reference}` : ""} · estimate ${displayMoney(item.estimated_price_cents)}`));
      const actions = element("div", "", "action-group");
      if (plan.status === "draft" && plan.items.length) actions.append(button("Activate", async () => { await api(`/api/treatment-plans/${plan.id}`, jsonOptions("PATCH", { status: "active" })); await refresh(); showMessage("Treatment plan activated."); }));
      if (["draft", "active"].includes(plan.status)) actions.append(button("Cancel plan", async () => { if (!window.confirm("Cancel this treatment plan?")) return; await api(`/api/treatment-plans/${plan.id}`, jsonOptions("PATCH", { status: "cancelled" })); await refresh(); showMessage("Treatment plan cancelled."); }, "secondary"));
      if (plan.status === "active") actions.append(button("Complete plan", async () => { await api(`/api/treatment-plans/${plan.id}`, jsonOptions("PATCH", { status: "completed" })); await refresh(); showMessage("Treatment plan completed."); }, "secondary"));
      row.append(detail, actions); planList.append(row);
    }
    renderRows(document.getElementById("note-records"), notes, 5, (note) => {
      const row = element("tr"); tableCell(row, note.created_at); tableCell(row, note.patient_id); tableCell(row, note.appointment_id);
      tableCell(row, note.body); tableCell(row, note.amends_note_id); return row;
    });
    const amendments = notes.map((note) => ({ id: note.id, patient_id: note.patient_id, label: `Note ${note.id} · patient ${note.patient_id} · ${note.created_at}` }));
    const amendmentSelect = document.querySelector('#note-form [name="amends_note_id"]');
    populate(amendmentSelect, amendments, (item) => item.label, "This is a new note");
  }
  notePatient.addEventListener("change", async () => {
    const patientId = Number(notePatient.value);
    const notes = pagedItems(await api(`/api/clinical-notes?patient_id=${patientId}&limit=200`));
    populate(document.querySelector('#note-form [name="amends_note_id"]'), notes.map((note) => ({ id: note.id, label: `Note ${note.id} · ${note.created_at}` })), (item) => item.label, "This is a new note");
    const patientAppointments = appointments.filter((visit) => visit.patient_id === patientId);
    populate(document.querySelector('#note-form [name="appointment_id"]'), patientAppointments, (visit) => `${visit.start_at} · ${visit.status}`, "No appointment selected");
  });
  document.getElementById("plan-form").addEventListener("submit", async (event) => {
    event.preventDefault(); const form = event.currentTarget; const raw = formValues(form); await api("/api/treatment-plans", jsonOptions("POST", { patient_id: Number(raw.patient_id), title: raw.title })); form.reset(); await refresh(); showMessage("Draft treatment plan created.");
  });
  document.getElementById("item-form").addEventListener("submit", async (event) => {
    event.preventDefault(); const form = event.currentTarget; const raw = formValues(form); const planId = raw.plan_id;
    await api(`/api/treatment-plans/${planId}/items`, jsonOptions("POST", { service_id: Number(raw.service_id), sequence: Number(raw.sequence), tooth_reference: raw.tooth_reference, notes: raw.notes })); form.reset(); await refresh(); showMessage("Treatment item added.");
  });
  document.getElementById("item-schedule-form").addEventListener("submit", async (event) => {
    event.preventDefault(); const form = event.currentTarget; const raw = formValues(form); await api(`/api/treatment-items/${raw.item_id}/schedule`, jsonOptions("POST", { appointment_id: Number(raw.appointment_id) })); form.reset(); await refresh(); showMessage("Treatment item linked to an appointment.");
  });
  document.getElementById("note-form").addEventListener("submit", async (event) => {
    event.preventDefault(); const form = event.currentTarget; const raw = formValues(form);
    await api("/api/clinical-notes", jsonOptions("POST", { patient_id: Number(raw.patient_id), appointment_id: raw.appointment_id || null, amends_note_id: raw.amends_note_id || null, body: raw.body }));
    form.reset(); await refresh(); showMessage("Clinical note added. Existing notes remain unchanged.");
  });
  await refresh();
}

async function setupWaitlist() {
  const form = document.getElementById("waitlist-form");
  const [patientData, serviceData, dentistData, roomData] = await Promise.all([api("/api/patients?active=true&limit=200"), api("/api/services?active=true&limit=200"), api("/api/dentists?active=true&limit=200"), api("/api/rooms?active=true&limit=200")]);
  const patients = pagedItems(patientData), services = pagedItems(serviceData), dentists = pagedItems(dentistData), rooms = pagedItems(roomData);
  populate(form.elements.patient_id, patients, (item) => `${item.first_name} ${item.last_name}`, "Choose patient");
  populate(form.elements.service_id, services, (item) => item.name, "Choose service");
  populate(form.elements.dentist_id, dentists, (item) => `${item.first_name} ${item.last_name}`, "Any qualified practitioner");
  async function refresh() {
    const data = await api("/api/waitlist?status=waiting&limit=200");
    renderRows(document.getElementById("waitlist-records"), pagedItems(data), 7, (entry) => {
      const row = element("tr"); tableCell(row, entry.patient_name); tableCell(row, entry.service_name); tableCell(row, entry.dentist_name || "Any qualified practitioner");
      tableCell(row, `${entry.earliest_date} to ${entry.latest_date}`); tableCell(row, `${entry.preferred_start}–${entry.preferred_end}`);
      const book = element("div", "", "waitlist-booking");
      const practitioner = document.createElement("select"); populate(practitioner, dentists, (person) => `${person.first_name} ${person.last_name}`, "Choose practitioner", entry.dentist_id || "");
      const room = document.createElement("select"); populate(room, rooms, (item) => item.name, "Choose room");
      const start = document.createElement("input"); start.type = "datetime-local"; start.setAttribute("aria-label", "Appointment start time");
      const matches = document.createElement("select"); matches.append(option("Search for matching slots", ""));
      const findMatches = button("Find available slots", async () => {
        const firstDate = [entry.earliest_date, localToday()].sort().at(-1);
        const query = new URLSearchParams({ from: firstDate, days: "14" });
        const result = await api(`/api/waitlist/${entry.id}/slots?${query}`);
        matches.replaceChildren(option(result.total ? `${result.total} matching slots · choose one` : "No matching slots in this 14-day window", ""));
        result.items.forEach((slot, index) => matches.append(option(`${slot.start_at} · practitioner ${slot.dentist_id} · ${rooms.find((item) => item.id === slot.room_id)?.name || `room ${slot.room_id}`}`, String(index))));
        matches._slots = result.items;
      }, "secondary");
      matches.addEventListener("change", () => {
        const slot = matches._slots?.[Number(matches.value)];
        if (!slot) return;
        practitioner.value = String(slot.dentist_id); room.value = String(slot.room_id); start.value = slot.start_at;
      });
      const schedule = button("Book this slot", async () => {
        await api(`/api/waitlist/${entry.id}/book`, jsonOptions("POST", { dentist_id: Number(practitioner.value), room_id: Number(room.value), start_at: start.value })); await refresh(); showMessage("Waiting-list entry booked.");
      });
      book.append(matches, findMatches, practitioner, room, start, schedule); tableCell(row, book);
      const actions = element("div", "", "action-group");
      actions.append(button("Withdraw", async () => { await api(`/api/waitlist/${entry.id}/withdraw`, jsonOptions("POST", {})); await refresh(); showMessage("Waiting-list entry withdrawn."); }, "secondary"));
      tableCell(row, actions); return row;
    });
  }
  form.addEventListener("submit", async (event) => {
    event.preventDefault(); const raw = formValues(form);
    const payload = { patient_id: Number(raw.patient_id), service_id: Number(raw.service_id), dentist_id: raw.dentist_id || null, earliest_date: raw.earliest_date, latest_date: raw.latest_date, preferred_start: raw.preferred_start, preferred_end: raw.preferred_end };
    await api("/api/waitlist", jsonOptions("POST", payload)); form.reset(); await refresh(); showMessage("Patient added to the waiting list.");
  });
  await refresh();
}

async function setupReminders() {
  const form = document.getElementById("reminder-form");
  const defaultTime = new Date(); defaultTime.setMinutes(0, 0, 0);
  form.elements.as_of.value = `${defaultTime.toISOString().slice(0, 16)}`;
  async function refresh() {
    const result = await api("/api/reminders?status=pending&limit=200");
    renderRows(document.getElementById("reminder-records"), pagedItems(result), 6, (reminder) => {
      const row = element("tr"); tableCell(row, reminder.due_at); tableCell(row, reminder.kind.replaceAll("_", " ")); tableCell(row, reminder.recipient); tableCell(row, reminder.body); tableCell(row, statusPill(reminder.status));
      tableCell(row, button("Acknowledge draft", async () => { await api(`/api/reminders/${reminder.id}/acknowledge`, jsonOptions("POST", {})); await refresh(); showMessage("Reminder draft acknowledged. No message was sent."); }, "secondary")); return row;
    });
  }
  form.addEventListener("submit", async (event) => { event.preventDefault(); const raw = formValues(form); const result = await api("/api/reminders/generate", jsonOptions("POST", { as_of: raw.as_of })); await refresh(); showMessage(`${result.created} local reminder draft(s) created. No message was sent.`); });
  await refresh();
}

async function setupReports() {
  const form = document.getElementById("report-form");
  const today = localToday(); form.elements.from.value = today; form.elements.to.value = today;
  async function runReports(from, to) {
    const start = new URLSearchParams({ date: from });
    const range = new URLSearchParams({ from, to });
    const [daily, utilization, revenue] = await Promise.all([api(`/api/reports/daily?${start}`), api(`/api/reports/utilization?${range}`), api(`/api/reports/revenue?${range}`)]);
    const dailyBox = document.getElementById("daily-report"); dailyBox.replaceChildren();
    dailyBox.append(element("p", `${daily.agenda.length} visits · ${daily.queue.length} checked in`));
    for (const [status, count] of Object.entries(daily.counts)) dailyBox.append(element("div", `${status.replaceAll("_", " ")}: ${count}`, "report-stat"));
    const utilizationBox = document.getElementById("utilization-report"); utilizationBox.replaceChildren();
    for (const practitioner of utilization.practitioners) utilizationBox.append(element("div", `${practitioner.dentist_name}: ${practitioner.appointment_minutes}/${practitioner.scheduled_capacity_minutes} min (${practitioner.utilization_percent}%)`, "report-stat"));
    for (const room of utilization.rooms) utilizationBox.append(element("div", `${room.room_name}: ${room.appointment_minutes}/${room.staffed_open_minutes} staffed minutes (${room.utilization_percent}%)`, "report-stat"));
    utilizationBox.append(element("p", utilization.room_capacity_note, "small-note"));
    const revenueBox = document.getElementById("revenue-report"); revenueBox.replaceChildren();
    for (const item of revenue.items) revenueBox.append(element("div", `${item.service_name}: ${item.completed_visits} visits · ${displayMoney(item.amount_cents)}`, "report-stat"));
    revenueBox.append(element("strong", `Estimate total: ${displayMoney(revenue.total_cents)}`));
  }
  form.addEventListener("submit", async (event) => { event.preventDefault(); const raw = formValues(form); await runReports(raw.from, raw.to); showMessage("Reports refreshed from persisted records."); });
  await runReports(today, today);
}

async function setupAudit() {
  async function refresh() {
    const query = new URLSearchParams({ limit: "200" });
    const action = document.getElementById("audit-action").value.trim();
    const entity = document.getElementById("audit-entity").value.trim();
    if (action) query.set("action", action); if (entity) query.set("entity_type", entity);
    const result = await api(`/api/audit?${query}`);
    renderRows(document.getElementById("audit-records"), pagedItems(result), 5, (event) => {
      const row = element("tr"); tableCell(row, event.created_at); tableCell(row, event.actor_id ? `Account ${event.actor_id}` : "System/import"); tableCell(row, event.action); tableCell(row, `${event.entity_type} #${event.entity_id ?? "—"}`); tableCell(row, JSON.stringify(event.metadata)); return row;
    });
  }
  document.getElementById("audit-filter").addEventListener("click", () => refresh().catch(reportFailure));
  await refresh();
}

async function setupAccounts() {
  const form = document.getElementById("account-form");
  const session = (await api("/api/session")).account;
  async function refresh() {
    const result = await api("/api/accounts?limit=200");
    renderRows(document.getElementById("account-records"), pagedItems(result), 6, (account) => {
      const row = element("tr"); tableCell(row, account.username); tableCell(row, account.display_name); tableCell(row, account.email);
      tableCell(row, account.role); tableCell(row, account.active ? "Yes" : "No");
      const actions = element("div", "", "action-group");
      actions.append(button("Edit", () => {
        document.getElementById("account-form-title").textContent = `Update account · ${account.username}`;
        form.elements.id.value = account.id; form.elements.username.value = account.username; form.elements.username.disabled = true;
        form.elements.display_name.value = account.display_name; form.elements.email.value = account.email;
        form.elements.role.value = account.role; form.elements.active.checked = account.active; form.elements.password.value = "";
        form.scrollIntoView({ behavior: "smooth", block: "center" });
      }, "secondary"));
      if (account.active) actions.append(button("Deactivate", async () => { await api(`/api/accounts/${account.id}`, jsonOptions("PATCH", { active: false })); await refresh(); showMessage("Account deactivated. Its current sessions will be rejected on their next request."); }, "secondary"));
      tableCell(row, actions); return row;
    });
  }
  form.addEventListener("reset", () => {
    setTimeout(() => { form.elements.username.disabled = false; document.getElementById("account-form-title").textContent = "Create local account"; }, 0);
  });
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const raw = formValues(form); const id = raw.id;
    const payload = { display_name: raw.display_name, email: raw.email, role: raw.role, active: form.elements.active.checked };
    if (raw.password) payload.password = raw.password;
    if (id) {
      await api(`/api/accounts/${id}`, jsonOptions("PATCH", payload));
      if (Number(id) === session.id && raw.password) {
        showMessage("Password updated. Sign in again to continue.");
        window.location.assign("/login");
        return;
      }
    } else {
      payload.username = raw.username;
      if (!raw.password) throw new Error("Enter a password of at least 12 characters for a new account.");
      await api("/api/accounts", jsonOptions("POST", payload));
    }
    form.reset(); await refresh(); showMessage(id ? "Account updated." : "Account created.");
  });
  await refresh();
}

const setupByPage = {
  home: setupHome, patients: setupPatients, dentists: setupDentists,
  appointments: setupAppointments, catalog: setupCatalog, schedule: setupSchedule,
  care: setupCare, waitlist: setupWaitlist, reminders: setupReminders,
  reports: setupReports, audit: setupAudit, accounts: setupAccounts,
};

if (setupByPage[page]) setupByPage[page]().catch(reportFailure);
window.addEventListener("unhandledrejection", (event) => {
  event.preventDefault();
  reportFailure(event.reason);
});
