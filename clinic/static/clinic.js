"use strict";

const page = document.body.dataset.page;
const role = document.body.dataset.role;
const responseBox = document.querySelector("#response");

const endpoints = {
  dentist: "/odontologos",
  patient: "/pacientes",
  appointment: "/turnos",
};

async function api(path, options = {}) {
  const response = await fetch(path, {
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  if (response.status === 401) {
    window.location.assign("/login");
    throw new Error("Tu sesión terminó. Vuelve a iniciar sesión.");
  }
  const result = response.status === 204 ? null : await response.json().catch(() => null);
  if (!response.ok) {
    throw new Error(result?.error || `La solicitud falló (${response.status}).`);
  }
  return result;
}

function announce(message, isError = false) {
  if (!responseBox) return;
  responseBox.textContent = message;
  responseBox.hidden = false;
  responseBox.classList.toggle("error", isError);
}

function clearAnnouncement() {
  if (!responseBox) return;
  responseBox.textContent = "";
  responseBox.hidden = true;
  responseBox.classList.remove("error");
}

function cell(row, value) {
  const item = document.createElement("td");
  item.textContent = value === null || value === undefined || value === "" ? "—" : String(value);
  row.append(item);
  return item;
}

function actionButton(label, action, danger = false) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = `small-button${danger ? " danger" : " secondary"}`;
  button.textContent = label;
  button.dataset.action = action;
  return button;
}

function emptyRow(body, columns, message) {
  const row = document.createElement("tr");
  const item = document.createElement("td");
  item.colSpan = columns;
  item.textContent = message;
  row.append(item);
  body.replaceChildren(row);
}

function addressSummary(address) {
  if (!address) return "—";
  return [address.calle, address.numero, address.localidad, address.provincia]
    .filter((part) => part !== null && part !== undefined && String(part).trim() !== "")
    .join(", ") || "—";
}

function dentistRow(dentist, body) {
  const row = document.createElement("tr");
  row.dataset.id = dentist.id;
  cell(row, dentist.id);
  cell(row, dentist.nombre);
  cell(row, dentist.apellido);
  cell(row, dentist.matricula);
  if (role === "ADMIN") {
    const actions = document.createElement("td");
    actions.className = "actions";
    actions.append(actionButton("Editar", "edit"), actionButton("Eliminar", "delete", true));
    row.append(actions);
  }
  body.append(row);
}

function patientRow(patient, body) {
  const row = document.createElement("tr");
  row.dataset.id = patient.id;
  cell(row, patient.id);
  cell(row, patient.nombre);
  cell(row, patient.apellido);
  cell(row, patient.email);
  cell(row, patient.dni);
  cell(row, patient.fechaIngreso);
  cell(row, addressSummary(patient.domicilio));
  if (role === "ADMIN") {
    const actions = document.createElement("td");
    actions.className = "actions";
    actions.append(actionButton("Editar", "edit"), actionButton("Eliminar", "delete", true));
    row.append(actions);
  }
  body.append(row);
}

function appointmentRow(appointment, body) {
  const row = document.createElement("tr");
  row.dataset.id = appointment.id;
  cell(row, appointment.id);
  cell(row, `${appointment.odontologo.nombre} ${appointment.odontologo.apellido}`);
  cell(row, `${appointment.paciente.nombre} ${appointment.paciente.apellido}`);
  cell(row, appointment.fecha);
  const actions = document.createElement("td");
  actions.className = "actions";
  actions.append(actionButton("Editar", "edit"), actionButton("Eliminar", "delete", true));
  row.append(actions);
  body.append(row);
}

async function loadDentists() {
  const body = document.querySelector("#records");
  const columns = role === "ADMIN" ? 5 : 4;
  try {
    const dentists = await api("/odontologos/listar");
    if (dentists.length === 0) return emptyRow(body, columns, "No hay odontólogos registrados.");
    body.replaceChildren();
    dentists.forEach((dentist) => dentistRow(dentist, body));
  } catch (error) {
    emptyRow(body, columns, error.message);
  }
}

async function loadPatients() {
  const body = document.querySelector("#records");
  const columns = role === "ADMIN" ? 8 : 7;
  try {
    const patients = await api("/pacientes/listar");
    if (patients.length === 0) return emptyRow(body, columns, "No hay pacientes registrados.");
    body.replaceChildren();
    patients.forEach((patient) => patientRow(patient, body));
  } catch (error) {
    emptyRow(body, columns, error.message);
  }
}

async function loadAppointments() {
  const body = document.querySelector("#records");
  try {
    const appointments = await api("/turnos");
    if (appointments.length === 0) return emptyRow(body, 5, "No hay turnos registrados.");
    body.replaceChildren();
    appointments.forEach((appointment) => appointmentRow(appointment, body));
  } catch (error) {
    emptyRow(body, 5, error.message);
  }
}

function formValues(form, entity) {
  const values = Object.fromEntries(new FormData(form).entries());
  if (entity === "dentist") {
    return { ...(values.id ? { id: Number(values.id) } : {}), nombre: values.nombre, apellido: values.apellido, matricula: values.matricula };
  }
  if (entity === "patient") {
    return {
      ...(values.id ? { id: Number(values.id) } : {}),
      nombre: values.nombre,
      apellido: values.apellido,
      email: values.email,
      dni: values.dni || null,
      fechaIngreso: values.fechaIngreso || null,
      domicilio: {
        calle: values.calle,
        numero: values.numero || null,
        localidad: values.localidad,
        provincia: values.provincia,
      },
    };
  }
  return {
    ...(values.id ? { id: Number(values.id) } : {}),
    paciente: { id: Number(values.paciente) },
    odontologo: { id: Number(values.odontologo) },
    fecha: values.fecha,
  };
}

function fillDentistForm(form, dentist) {
  form.elements.id.value = dentist.id;
  form.elements.nombre.value = dentist.nombre;
  form.elements.apellido.value = dentist.apellido;
  form.elements.matricula.value = dentist.matricula;
}

function fillPatientForm(form, patient) {
  form.elements.id.value = patient.id;
  form.elements.nombre.value = patient.nombre;
  form.elements.apellido.value = patient.apellido;
  form.elements.email.value = patient.email || "";
  form.elements.dni.value = patient.dni || "";
  form.elements.fechaIngreso.value = patient.fechaIngreso || "";
  const address = patient.domicilio || {};
  form.elements.calle.value = address.calle || "";
  form.elements.numero.value = address.numero || "";
  form.elements.localidad.value = address.localidad || "";
  form.elements.provincia.value = address.provincia || "";
}

async function fillAppointmentSelectors(form) {
  const [dentists, patients] = await Promise.all([
    api("/odontologos/listar"),
    api("/pacientes/listar"),
  ]);
  const dentistSelect = form.elements.odontologo;
  const patientSelect = form.elements.paciente;
  dentistSelect.replaceChildren(new Option("Selecciona un odontólogo", ""));
  patientSelect.replaceChildren(new Option("Selecciona un paciente", ""));
  dentists.forEach((dentist) => dentistSelect.add(new Option(`${dentist.nombre} ${dentist.apellido}`, dentist.id)));
  patients.forEach((patient) => patientSelect.add(new Option(`${patient.nombre} ${patient.apellido}`, patient.id)));
}

function fillAppointmentForm(form, appointment) {
  form.elements.id.value = appointment.id;
  form.elements.odontologo.value = appointment.odontologo.id;
  form.elements.paciente.value = appointment.paciente.id;
  form.elements.fecha.value = appointment.fecha;
}

function showEditPanel() {
  document.querySelector("#edit-panel").hidden = false;
  document.querySelector("#edit-panel").scrollIntoView({ behavior: "smooth", block: "start" });
}

function registerCreateForm() {
  const form = document.querySelector("#create-form");
  if (!form) return;
  if (form.dataset.entity === "appointment") {
    fillAppointmentSelectors(form).catch((error) => announce(error.message, true));
  }
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    clearAnnouncement();
    try {
      await api(endpoints[form.dataset.entity], {
        method: "POST",
        body: JSON.stringify(formValues(form, form.dataset.entity)),
      });
      announce("Registro guardado correctamente.");
      form.reset();
    } catch (error) {
      announce(error.message, true);
    }
  });
}

function registerEditForm() {
  const form = document.querySelector("#edit-form");
  if (!form) return;
  if (form.dataset.entity === "appointment") {
    fillAppointmentSelectors(form).catch((error) => announce(error.message, true));
  }
  document.querySelector("[data-cancel]")?.addEventListener("click", () => {
    document.querySelector("#edit-panel").hidden = true;
  });
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    clearAnnouncement();
    try {
      await api(endpoints[form.dataset.entity], {
        method: "PUT",
        body: JSON.stringify(formValues(form, form.dataset.entity)),
      });
      announce("Cambios guardados correctamente.");
      document.querySelector("#edit-panel").hidden = true;
      if (page === "dentists") await loadDentists();
      if (page === "patients") await loadPatients();
      if (page === "appointments") await loadAppointments();
    } catch (error) {
      announce(error.message, true);
    }
  });
}

function registerRowActions(entity) {
  const body = document.querySelector("#records");
  if (!body) return;
  body.addEventListener("click", async (event) => {
    const button = event.target.closest("button[data-action]");
    if (!button) return;
    const id = Number(button.closest("tr").dataset.id);
    clearAnnouncement();
    try {
      if (button.dataset.action === "delete") {
        await api(`${endpoints[entity]}/${id}`, { method: "DELETE" });
        announce("Registro eliminado correctamente.");
        if (entity === "dentist") await loadDentists();
        if (entity === "patient") await loadPatients();
        if (entity === "appointment") await loadAppointments();
        return;
      }
      const record = await api(`${endpoints[entity]}/${id}`);
      const form = document.querySelector("#edit-form");
      if (entity === "dentist") fillDentistForm(form, record);
      if (entity === "patient") fillPatientForm(form, record);
      if (entity === "appointment") {
        await fillAppointmentSelectors(form);
        fillAppointmentForm(form, record);
      }
      showEditPanel();
    } catch (error) {
      announce(error.message, true);
    }
  });
}

if (page === "dentists") {
  loadDentists();
  registerRowActions("dentist");
}
if (page === "patients") {
  loadPatients();
  registerRowActions("patient");
}
if (page === "appointments") {
  loadAppointments();
  registerRowActions("appointment");
}
registerCreateForm();
registerEditForm();
