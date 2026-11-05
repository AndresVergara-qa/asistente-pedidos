import ReactDOM from 'react-dom/client';
import React, { useState, useEffect, useMemo } from 'react';
import {
  Building2,
  Clock,
  ShieldAlert,
  Lock,
  Unlock,
  UserCheck,
  Utensils,
  RotateCcw,
  LogOut,
  LogIn,
  Trash2,
  Plus,
  Search,
  Download,
  Sliders,
  Table,
  ListChecks,
  CheckCircle2,
  AlertCircle,
  MapPin,
  Users,
  Briefcase,
  KeyRound,
  X,
  Edit3,
  Save,
  RefreshCw,
  FileSpreadsheet,
  Copy,
  Check,
  ShieldCheck,
  ExternalLink
} from 'lucide-react';

// --- DATOS INICIALES DEL ERP MULTI-EMPRESA ---
const COMPANIES_INIT = [
  { id: 'COMP-01', name: 'Macna Group International Logistics', active: true },
  { id: 'COMP-02', name: 'Convenient Distributor LLC', active: true }
];

const STATIONS_INIT = [
  { id: 'ST-01', name: 'Planta Principal - Portón A', active: true },
  { id: 'ST-02', name: 'Oficinas Administrativas (Miami / Remoto)', active: true },
  { id: 'ST-03', name: 'Almacén Central / Despacho', active: true }
];

const DEFAULT_DEPARTMENTS = [
  'Logística y Almacén',
  'Finanzas y Contabilidad',
  'Administración',
  'Operaciones',
  'Control de Calidad',
  'Compras y Procura',
  'Ventas y Comercial',
  'Recursos Humanos',
  'Seguridad e Higiene',
  'Mantenimiento Industrial',
  'IT / Sistemas',
  'OTRO_PERSONALIZADO'
];

const EMPLOYEES_INIT = [
  {
    id: 'EMP-001',
    name: 'Daniel Montero',
    companyId: 'COMP-02',
    department: 'Finanzas y Contabilidad',
    role: 'Administrador General ERP',
    active: true,
    inactiveReason: '',
    isSupervisor: true
  },
  {
    id: 'EMP-1001',
    name: 'Carlos Mendoza',
    companyId: 'COMP-01',
    department: 'Logística y Almacén',
    role: 'Coordinador de Tráfico Internacional',
    active: true,
    inactiveReason: '',
    isSupervisor: true
  },
  {
    id: 'EMP-1002',
    name: 'Valeria López',
    companyId: 'COMP-01',
    department: 'Finanzas y Contabilidad',
    role: 'Analista Financiero Senior',
    active: true,
    inactiveReason: '',
    isSupervisor: true
  },
  {
    id: 'EMP-1003',
    name: 'Roberto Gómez',
    companyId: 'COMP-02',
    department: 'Logística y Almacén',
    role: 'Jefe de Almacén y Montacargas',
    active: true,
    inactiveReason: '',
    isSupervisor: false
  },
  {
    id: 'EMP-1004',
    name: 'Ana María Silva',
    companyId: 'COMP-02',
    department: 'Operaciones',
    role: 'Supervisora de Distribución',
    active: true,
    inactiveReason: '',
    isSupervisor: true
  },
  {
    id: 'EMP-1005',
    name: 'Elena Ríos',
    companyId: 'COMP-01',
    department: 'Administración',
    role: 'Especialista de Nómina y ERP',
    active: true,
    inactiveReason: '',
    isSupervisor: false
  }
];

function App() {
  // --- NAVEGACIÓN Y AUTENTICACIÓN DE TORRE DE CONTROL ---
  const [viewMode, setViewMode] = useState('KIOSK'); // 'KIOSK' | 'TOWER'
  const [isTowerUnlocked, setIsTowerUnlocked] = useState(false);
  const [activeSupervisor, setActiveSupervisor] = useState(null);

  // Modal de Login para Torre de Control
  const [authModalOpen, setAuthModalOpen] = useState(false);
  const [authSelectedUser, setAuthSelectedUser] = useState('');
  const [authPassword, setAuthPassword] = useState('');
  const [authError, setAuthError] = useState('');
  const [systemPassword, setSystemPassword] = useState(() => {
    try {
      return localStorage.getItem('erp_tower_pwd') || 'admin123';
    } catch (e) {
      return 'admin123';
    }
  });
  const [isOtpUsed, setIsOtpUsed] = useState(() => {
    try {
      return localStorage.getItem('erp_otp_used') === 'true';
    } catch (e) {
      return false;
    }
  });
  const [forcePasswordChangeModal, setForcePasswordChangeModal] = useState(false);
  const [newPasswordInput, setNewPasswordInput] = useState('');
  const [confirmPasswordInput, setConfirmPasswordInput] = useState('');
  const [passwordChangeError, setPasswordChangeError] = useState('');

  // --- INTEGRACIÓN GOOGLE SHEETS ---
  const [googleSheetUrl, setGoogleSheetUrl] = useState(() => {
    try {
      return localStorage.getItem('erp_gsheet_url') || '';
    } catch (e) {
      return '';
    }
  });
  const [googleSheetDocUrl, setGoogleSheetDocUrl] = useState(() => {
    try {
      return localStorage.getItem('erp_gsheet_doc_url') || '';
    } catch (e) {
      return '';
    }
  });
  const [syncStatus, setSyncStatus] = useState('IDLE');

  // --- ESTADO DE EDICIÓN DE CONSOLAS ---
  const [editModal, setEditModal] = useState({
    open: false,
    type: null,
    originalId: '',
    id: '',
    name: '',
    companyId: '',
    departmentSelect: '',
    customDept: '',
    role: '',
    isSupervisor: false
  });

  // --- PERSISTENCIA DE DATOS ---
  const [companies, setCompanies] = useState(() => {
    try {
      const saved = localStorage.getItem('erp_companies_v3');
      return saved ? JSON.parse(saved) : COMPANIES_INIT;
    } catch (e) {
      return COMPANIES_INIT;
    }
  });

  const [employees, setEmployees] = useState(() => {
    try {
      const saved = localStorage.getItem('erp_employees_v3');
      return saved ? JSON.parse(saved) : EMPLOYEES_INIT;
    } catch (e) {
      return EMPLOYEES_INIT;
    }
  });

  const [stations, setStations] = useState(() => {
    try {
      const saved = localStorage.getItem('erp_stations_v3');
      return saved ? JSON.parse(saved) : STATIONS_INIT;
    } catch (e) {
      return STATIONS_INIT;
    }
  });

  const [auditLog, setAuditLog] = useState(() => {
    try {
      const saved = localStorage.getItem('erp_logs_v3');
      return saved ? JSON.parse(saved) : [];
    } catch (e) {
      return [];
    }
  });

  const [employeeStatusMap, setEmployeeStatusMap] = useState(() => {
    try {
      const saved = localStorage.getItem('erp_status_v3');
      if (saved) return JSON.parse(saved);
    } catch (e) {}
    const map = {};
    EMPLOYEES_INIT.forEach(e => {
      map[e.id] = { state: 'AUSENTE', lastAction: null };
    });
    return map;
  });

  useEffect(() => {
    try {
      localStorage.setItem('erp_companies_v3', JSON.stringify(companies));
      localStorage.setItem('erp_employees_v3', JSON.stringify(employees));
      localStorage.setItem('erp_stations_v3', JSON.stringify(stations));
      localStorage.setItem('erp_logs_v3', JSON.stringify(auditLog));
      localStorage.setItem('erp_status_v3', JSON.stringify(employeeStatusMap));
      localStorage.setItem('erp_tower_pwd', systemPassword);
      localStorage.setItem('erp_otp_used', String(isOtpUsed));
      localStorage.setItem('erp_gsheet_url', googleSheetUrl);
      localStorage.setItem('erp_gsheet_doc_url', googleSheetDocUrl);
    } catch (e) {}
  }, [companies, employees, stations, auditLog, employeeStatusMap, systemPassword, isOtpUsed, googleSheetUrl, googleSheetDocUrl]);

  const pushToGoogleSheets = async (payloadType, dataPayload) => {
    if (!googleSheetUrl || !googleSheetUrl.trim().startsWith('https://script.google.com')) {
      return false;
    }
    setSyncStatus('SYNCING');
    try {
      await fetch(googleSheetUrl.trim(), {
        method: 'POST',
        mode: 'no-cors',
        headers: { 'Content-Type': 'text/plain;charset=utf-8' },
        body: JSON.stringify({
          type: payloadType,
          timestampMiami: getMiamiTime(new Date()),
          dateMiami: getMiamiDate(new Date()),
          payload: dataPayload
        })
      });
      setSyncStatus('SUCCESS');
      setTimeout(() => setSyncStatus('IDLE'), 3500);
      return true;
    } catch (err) {
      setSyncStatus('ERROR');
      return false;
    }
  };

  const handleFullSyncToSheets = () => {
    pushToGoogleSheets('FULL_SYNC', { companies, stations, employees, auditLog });
  };

  const [currentTime, setCurrentTime] = useState(new Date());
  useEffect(() => {
    const timer = setInterval(() => setCurrentTime(new Date()), 1000);
    return () => clearInterval(timer);
  }, []);

  const getMiamiTime = (dateObj) => {
    return dateObj.toLocaleTimeString('es-ES', {
      timeZone: 'America/New_York',
      hour12: false,
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit'
    });
  };

  const getMiamiDate = (dateObj) => {
    return dateObj.toLocaleDateString('es-ES', {
      timeZone: 'America/New_York',
      weekday: 'short',
      year: 'numeric',
      month: 'short',
      day: 'numeric'
    });
  };

  const [selectedEmp, setSelectedEmp] = useState('');
  const [selectedAction, setSelectedAction] = useState('CHECK_IN');
  const [selectedStation, setSelectedStation] = useState(stations[0]?.id || 'ST-01');
  const [incidentNote, setIncidentNote] = useState('');
  const [feedback, setFeedback] = useState(null);

  const [activeTab, setActiveTab] = useState('CONSOLA');
  const [searchTerm, setSearchTerm] = useState('');
  const [selectedCompanyFilter, setSelectedCompanyFilter] = useState('ALL');
  const [selectedDeptFilter, setSelectedDeptFilter] = useState('ALL');

  const [newEmpId, setNewEmpId] = useState('');
  const [newEmpName, setNewEmpName] = useState('');
  const [newEmpCompany, setNewEmpCompany] = useState(companies[0]?.id || 'COMP-01');
  const [newEmpDeptSelect, setNewEmpDeptSelect] = useState('Logística y Almacén');
  const [customDeptInput, setCustomDeptInput] = useState('');
  const [newEmpRole, setNewEmpRole] = useState('');
  const [newEmpIsSupervisor, setNewEmpIsSupervisor] = useState(false);

  const [newStationId, setNewStationId] = useState('');
  const [newStationName, setNewStationName] = useState('');

  const [newCompanyId, setNewCompanyId] = useState('');
  const [newCompanyName, setNewCompanyName] = useState('');

  const [deleteModal, setDeleteModal] = useState({ open: false, type: null, id: null, name: '' });
  const [inactiveModal, setInactiveModal] = useState({ open: false, empId: null, empName: '', reason: '' });

  const handleSwitchView = (targetView) => {
    if (targetView === 'KIOSK') {
      setViewMode('KIOSK');
      return;
    }
    if (targetView === 'TOWER') {
      if (isTowerUnlocked) {
        setViewMode('TOWER');
      } else {
        const firstSupervisor = employees.find(e => e.isSupervisor && e.active !== false);
        setAuthSelectedUser(firstSupervisor ? firstSupervisor.id : '');
        setAuthPassword('');
        setAuthError('');
        setAuthModalOpen(true);
      }
    }
  };

  const handleConfirmTowerAuth = (e) => {
    e.preventDefault();
    if (!authSelectedUser) {
      setAuthError('Seleccione un supervisor autorizado.');
      return;
    }
    const emp = employees.find(x => x.id === authSelectedUser);
    if (!emp || !emp.isSupervisor || emp.active === false) {
      setAuthError('Este colaborador no posee permisos.');
      return;
    }
    if (authPassword !== systemPassword) {
      setAuthError('Contraseña incorrecta.');
      return;
    }
    setIsTowerUnlocked(true);
    setActiveSupervisor(emp);
    setAuthModalOpen(false);
    setViewMode('TOWER');
    if (!isOtpUsed) {
      setNewPasswordInput('');
      setConfirmPasswordInput('');
      setPasswordChangeError('');
      setForcePasswordChangeModal(true);
    }
  };

  const handleSaveNewPassword = (e) => {
    e.preventDefault();
    if (newPasswordInput.trim().length < 4) {
      setPasswordChangeError('Mínimo 4 caracteres.');
      return;
    }
    if (newPasswordInput.trim() === 'admin123') {
      setPasswordChangeError('No puede reutilizar admin123.');
      return;
    }
    if (newPasswordInput !== confirmPasswordInput) {
      setPasswordChangeError('Las contraseñas no coinciden.');
      return;
    }
    setSystemPassword(newPasswordInput.trim());
    setIsOtpUsed(true);
    setForcePasswordChangeModal(false);
  };

  const handleLockTower = () => {
    setIsTowerUnlocked(false);
    setActiveSupervisor(null);
    setViewMode('KIOSK');
  };

  const handleRegisterEvent = (e) => {
    e.preventDefault();
    if (!selectedEmp) {
      setFeedback({ type: 'error', message: 'Seleccione un colaborador.' });
      return;
    }
    const emp = employees.find(x => x.id === selectedEmp);
    if (!emp || emp.active === false) {
      setFeedback({ type: 'error', message: 'El colaborador está inactivo.' });
      return;
    }

    const currentEmpState = employeeStatusMap[selectedEmp] || { state: 'AUSENTE', lastAction: null };
    const nowFormatted = getMiamiTime(currentTime);

    let newState = currentEmpState.state;
    let actionLabel = '';

    if (selectedAction === 'CHECK_IN') {
      if (currentEmpState.state === 'PRESENTE') {
        setFeedback({ type: 'error', message: 'Ya registra entrada activa.' });
        return;
      }
      newState = 'PRESENTE';
      actionLabel = 'Entrada (Check-In)';
    } else if (selectedAction === 'LUNCH_START') {
      if (currentEmpState.state !== 'PRESENTE') {
        setFeedback({ type: 'error', message: 'Debe marcar entrada primero.' });
        return;
      }
      newState = 'ALMUERZO';
      actionLabel = 'Inicio Almuerzo';
    } else if (selectedAction === 'LUNCH_END') {
      if (currentEmpState.state !== 'ALMUERZO') {
        setFeedback({ type: 'error', message: 'No está en almuerzo.' });
        return;
      }
      newState = 'PRESENTE';
      actionLabel = 'Retorno Almuerzo';
    } else if (selectedAction === 'CHECK_OUT') {
      if (currentEmpState.state === 'AUSENTE' || currentEmpState.state === 'FINALIZADO') {
        setFeedback({ type: 'error', message: 'No hay jornada activa.' });
        return;
      }
      newState = 'FINALIZADO';
      actionLabel = 'Salida (Check-Out)';
    }

    setEmployeeStatusMap(prev => ({
      ...prev,
      [selectedEmp]: { state: newState, lastAction: nowFormatted }
    }));

    const stationObj = stations.find(s => s.id === selectedStation);
    const companyObj = companies.find(c => c.id === emp.companyId);

    const logEntry = {
      id: 'LOG-' + Math.floor(100000 + Math.random() * 900000),
      empId: emp.id,
      empName: emp.name,
      companyId: emp.companyId,
      companyName: companyObj ? companyObj.name : 'General',
      dept: emp.department,
      action: selectedAction,
      actionLabel,
      timestamp: nowFormatted,
      date: getMiamiDate(currentTime),
      station: stationObj ? stationObj.name : selectedStation,
      note: incidentNote || 'Sin observaciones'
    };

    setAuditLog(prev => [logEntry, ...prev]);
    pushToGoogleSheets('ATTENDANCE_LOG', logEntry);
    setFeedback({ type: 'success', message: `Marcaje registrado: [${actionLabel}] — ${emp.name}` });
    setIncidentNote('');
    setTimeout(() => setFeedback(null), 4000);
  };

  const handleForceClockOut = (empId) => {
    const emp = employees.find(x => x.id === empId);
    if (!emp) return;
    const nowFormatted = getMiamiTime(currentTime);
    const companyObj = companies.find(c => c.id === emp.companyId);

    setEmployeeStatusMap(prev => ({
      ...prev,
      [empId]: { state: 'FINALIZADO', lastAction: nowFormatted }
    }));

    const logEntry = {
      id: 'LOG-' + Math.floor(100000 + Math.random() * 900000),
      empId: emp.id,
      empName: emp.name,
      companyId: emp.companyId,
      companyName: companyObj ? companyObj.name : 'General',
      dept: emp.department,
      action: 'CHECK_OUT',
      actionLabel: 'Salida Forzada (Torre)',
      timestamp: nowFormatted,
      date: getMiamiDate(currentTime),
      station: `Torre de Control (${activeSupervisor?.name || 'Admin'})`,
      note: 'Cierre manual'
    };
    setAuditLog(prev => [logEntry, ...prev]);
    pushToGoogleSheets('ATTENDANCE_LOG', logEntry);
  };

  const openEditRecord = (type, item) => {
    if (type === 'EMPLOYEE') {
      const isStandardDept = DEFAULT_DEPARTMENTS.includes(item.department) && item.department !== 'OTRO_PERSONALIZADO';
      setEditModal({
        open: true,
        type: 'EMPLOYEE',
        originalId: item.id,
        id: item.id,
        name: item.name,
        companyId: item.companyId || (companies[0]?.id || ''),
        departmentSelect: isStandardDept ? item.department : 'OTRO_PERSONALIZADO',
        customDept: isStandardDept ? '' : item.department,
        role: item.role || '',
        isSupervisor: Boolean(item.isSupervisor)
      });
    } else if (type === 'COMPANY') {
      setEditModal({
        open: true,
        type: 'COMPANY',
        originalId: item.id,
        id: item.id,
        name: item.name,
        companyId: '',
        departmentSelect: '',
        customDept: '',
        role: '',
        isSupervisor: false
      });
    } else if (type === 'STATION') {
      setEditModal({
        open: true,
        type: 'STATION',
        originalId: item.id,
        id: item.id,
        name: item.name,
        companyId: '',
        departmentSelect: '',
        customDept: '',
        role: '',
        isSupervisor: false
      });
    }
  };

  const handleSaveEditModal = (e) => {
    e.preventDefault();
    const { type, originalId, id, name, companyId, departmentSelect, customDept, role, isSupervisor } = editModal;
    const cleanId = id.trim().toUpperCase();
    const cleanName = name.trim();
    if (!cleanId || !cleanName) return;

    if (type === 'EMPLOYEE') {
      const finalDept = departmentSelect === 'OTRO_PERSONALIZADO' ? customDept.trim() || 'General' : departmentSelect;
      setEmployees(prev =>
        prev.map(emp =>
          emp.id === originalId
            ? { ...emp, id: cleanId, name: cleanName, companyId, department: finalDept, role: role.trim(), isSupervisor }
            : emp
        )
      );
      if (originalId !== cleanId) {
        setEmployeeStatusMap(prev => {
          const copy = { ...prev };
          copy[cleanId] = copy[originalId] || { state: 'AUSENTE', lastAction: null };
          delete copy[originalId];
          return copy;
        });
      }
    } else if (type === 'COMPANY') {
      setCompanies(prev =>
        prev.map(comp => (comp.id === originalId ? { ...comp, id: cleanId, name: cleanName } : comp))
      );
      if (originalId !== cleanId) {
        setEmployees(prev =>
          prev.map(emp => (emp.companyId === originalId ? { ...emp, companyId: cleanId } : emp))
        );
      }
    } else if (type === 'STATION') {
      setStations(prev =>
        prev.map(st => (st.id === originalId ? { ...st, id: cleanId, name: cleanName } : st))
      );
    }
    setEditModal({
      open: false,
      type: null,
      originalId: '',
      id: '',
      name: '',
      companyId: '',
      departmentSelect: '',
      customDept: '',
      role: '',
      isSupervisor: false
    });
  };

  const handleAddEmployee = (e) => {
    e.preventDefault();
    if (!newEmpId.trim() || !newEmpName.trim() || !newEmpRole.trim()) return;
    const finalDept = newEmpDeptSelect === 'OTRO_PERSONALIZADO' ? customDeptInput.trim() || 'General' : newEmpDeptSelect;
    const newEmp = {
      id: newEmpId.trim().toUpperCase(),
      name: newEmpName.trim(),
      companyId: newEmpCompany,
      department: finalDept,
      role: newEmpRole.trim(),
      active: true,
      inactiveReason: '',
      isSupervisor: newEmpIsSupervisor
    };
    setEmployees(prev => [...prev, newEmp]);
    setEmployeeStatusMap(prev => ({ ...prev, [newEmp.id]: { state: 'AUSENTE', lastAction: null } }));
    setNewEmpId('');
    setNewEmpName('');
    setNewEmpRole('');
    setCustomDeptInput('');
    setNewEmpIsSupervisor(false);
  };

  const handleToggleEmployee = (empId) => {
    const emp = employees.find(e => e.id === empId);
    if (!emp) return;
    if (emp.active !== false) {
      setInactiveModal({ open: true, empId: emp.id, empName: emp.name, reason: '' });
    } else {
      setEmployees(prev =>
        prev.map(e => (e.id === empId ? { ...e, active: true, inactiveReason: '' } : e))
      );
    }
  };

  const handleConfirmInactivation = (e) => {
    e.preventDefault();
    if (!inactiveModal.reason.trim()) return;
    setEmployees(prev =>
      prev.map(emp =>
        emp.id === inactiveModal.empId
          ? { ...emp, active: false, inactiveReason: inactiveModal.reason.trim() }
          : emp
      )
    );
    if (selectedEmp === inactiveModal.empId) setSelectedEmp('');
    setInactiveModal({ open: false, empId: null, empName: '', reason: '' });
  };

  const handleAddStation = (e) => {
    e.preventDefault();
    if (!newStationId.trim() || !newStationName.trim()) return;
    setStations(prev => [
      ...prev,
      { id: newStationId.trim().toUpperCase(), name: newStationName.trim(), active: true }
    ]);
    setNewStationId('');
    setNewStationName('');
  };

  const handleToggleStation = (stId) => {
    setStations(prev => prev.map(st => (st.id === stId ? { ...st, active: !st.active } : st)));
  };

  const handleAddCompany = (e) => {
    e.preventDefault();
    if (!newCompanyId.trim() || !newCompanyName.trim()) return;
    setCompanies(prev => [
      ...prev,
      { id: newCompanyId.trim().toUpperCase(), name: newCompanyName.trim(), active: true }
    ]);
    setNewCompanyId('');
    setNewCompanyName('');
  };

  const handleToggleCompany = (compId) => {
    setCompanies(prev => prev.map(c => (c.id === compId ? { ...c, active: !c.active } : c)));
  };

  const requestDelete = (type, id, name) => {
    setDeleteModal({ open: true, type, id, name });
  };

  const confirmDeleteAction = () => {
    const { type, id } = deleteModal;
    if (type === 'EMPLOYEE') {
      setEmployees(prev => prev.filter(e => e.id !== id));
      setEmployeeStatusMap(prev => {
        const copy = { ...prev };
        delete copy[id];
        return copy;
      });
      if (selectedEmp === id) setSelectedEmp('');
    } else if (type === 'STATION') {
      setStations(prev => prev.filter(s => s.id !== id));
    } else if (type === 'COMPANY') {
      setCompanies(prev => prev.filter(c => c.id !== id));
    }
    setDeleteModal({ open: false, type: null, id: null, name: '' });
  };

  const exportCSV = () => {
    let csv = 'data:text/csv;charset=utf-8,ID,Colaborador,Empresa,Departamento,Evento,Fecha,Hora,Estacion,Nota\n';
    auditLog.forEach(r => {
      csv += `"${r.empId}","${r.empName}","${r.companyName || ''}","${r.dept}","${r.actionLabel}","${r.date}","${r.timestamp}","${r.station}","${r.note}"\n`;
    });
    const link = document.createElement('a');
    link.setAttribute('href', encodeURI(csv));
    link.setAttribute('download', `Reporte_Marcajes_ERP.csv`);
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  };

  const allDepartmentsList = useMemo(() => {
    const set = new Set(DEFAULT_DEPARTMENTS.filter(d => d !== 'OTRO_PERSONALIZADO'));
    employees.forEach(e => {
      if (e.department) set.add(e.department);
    });
    return Array.from(set);
  }, [employees]);

  const kpis = useMemo(() => {
    const activeEmps = employees.filter(e => e.active !== false);
    const total = activeEmps.length;
    let presentes = 0,
      almuerzo = 0,
      finalizados = 0,
      ausentes = 0;
    activeEmps.forEach(emp => {
      const st = employeeStatusMap[emp.id]?.state || 'AUSENTE';
      if (st === 'PRESENTE') presentes++;
      else if (st === 'ALMUERZO') almuerzo++;
      else if (st === 'FINALIZADO') finalizados++;
      else ausentes++;
    });
    return { total, presentes, almuerzo, finalizados, ausentes };
  }, [employees, employeeStatusMap]);

  const filteredEmployees = useMemo(() => {
    return employees.filter(emp => {
      const matchesSearch =
        emp.name.toLowerCase().includes(searchTerm.toLowerCase()) ||
        emp.id.toLowerCase().includes(searchTerm.toLowerCase());
      const matchesCompany =
        selectedCompanyFilter === 'ALL' || emp.companyId === selectedCompanyFilter;
      const matchesDept = selectedDeptFilter === 'ALL' || emp.department === selectedDeptFilter;
      return matchesSearch && matchesCompany && matchesDept;
    });
  }, [employees, searchTerm, selectedCompanyFilter, selectedDeptFilter]);

  return (
    <div className="min-h-screen flex flex-col bg-slate-900 text-slate-100 font-sans">
      <header className="bg-slate-950 border-b border-slate-800 px-6 py-4 flex flex-col sm:flex-row justify-between items-center gap-4 sticky top-0 z-40">
        <div className="flex items-center space-x-3">
          <div className="p-2.5 bg-blue-600/20 text-blue-400 rounded-xl border border-blue-500/30">
            <Building2 className="w-6 h-6" />
          </div>
          <div>
            <div className="flex items-center space-x-2">
              <h1 className="text-lg font-bold text-slate-100 tracking-tight">
                NEXUS<span className="text-blue-400">ERP</span>
              </h1>
              <span className="text-[10px] font-mono uppercase px-2 py-0.5 rounded-full bg-blue-950 text-blue-300 border border-blue-500/30">
                Multi-Empresa
              </span>
            </div>
            <p className="text-xs text-slate-400 mt-0.5">Control Operativo & Tiempo de Asistencia • Base Miami (US)</p>
          </div>
        </div>

        <div className="flex items-center bg-slate-900 border border-slate-800 rounded-xl p-1">
          <button
            type="button"
            onClick={() => handleSwitchView('KIOSK')}
            className={`flex items-center space-x-2 px-4 py-2 rounded-lg text-sm font-medium transition-all ${
              viewMode === 'KIOSK' ? 'bg-blue-600 text-white shadow-md' : 'text-slate-400 hover:text-slate-200'
            }`}
          >
            <Clock className="w-4 h-4" />
            <span>Link 1: Kiosco Registro</span>
          </button>
          <button
            type="button"
            onClick={() => handleSwitchView('TOWER')}
            className={`flex items-center space-x-2 px-4 py-2 rounded-lg text-sm font-medium transition-all ${
              viewMode === 'TOWER' ? 'bg-indigo-600 text-white shadow-md' : 'text-slate-400 hover:text-slate-200'
            }`}
          >
            {isTowerUnlocked ? <Unlock className="w-4 h-4" /> : <Lock className="w-4 h-4" />}
            <span>Link 2: Torre de Control</span>
          </button>
          {isTowerUnlocked && viewMode === 'TOWER' && (
            <button
              type="button"
              onClick={handleLockTower}
              className="ml-2 px-3 py-2 rounded-lg bg-rose-950/80 hover:bg-rose-900 text-rose-300 border border-rose-500/40 text-xs font-semibold flex items-center space-x-1.5 transition-all"
            >
              <Lock className="w-3.5 h-3.5" />
              <span>Bloquear</span>
            </button>
          )}
        </div>
      </header>

      <main className="flex-1 p-4 md:p-8 max-w-7xl mx-auto w-full">
        {viewMode === 'KIOSK' && (
          <div className="max-w-2xl mx-auto space-y-6">
            <div className="bg-slate-800/70 border border-slate-700/80 rounded-2xl p-6 flex flex-col sm:flex-row items-center justify-between gap-4 shadow-xl">
              <div>
                <h2 className="text-xl font-bold text-white flex items-center gap-2.5">
                  <LogIn className="w-6 h-6 text-blue-400" />
                  <span>Portal de Registro de Personal</span>
                </h2>
                <p className="text-xs text-slate-400 mt-1">Punto de marcaje digital para entrada, turnos de descanso y salida.</p>
              </div>
              <div className="bg-slate-950/90 border border-slate-700/80 rounded-xl px-5 py-3 text-center min-w-[210px]">
                <div className="text-[10px] font-mono uppercase tracking-wider text-slate-400">HORA OFICIAL MIAMI (US)</div>
                <div className="text-2xl font-mono font-bold text-cyan-400 tracking-wider mt-0.5">{getMiamiTime(currentTime)}</div>
                <div className="text-[10px] text-slate-500 mt-0.5">{getMiamiDate(currentTime)}</div>
              </div>
            </div>

            {feedback && (
              <div
                className={`p-4 rounded-xl border flex items-center space-x-3 ${
                  feedback.type === 'success'
                    ? 'bg-emerald-950/70 border-emerald-500/50 text-emerald-300'
                    : 'bg-rose-950/70 border-rose-500/50 text-rose-300'
                }`}
              >
                {feedback.type === 'success' ? (
                  <CheckCircle2 className="w-5 h-5 text-emerald-400 shrink-0" />
                ) : (
                  <AlertCircle className="w-5 h-5 text-rose-400 shrink-0" />
                )}
                <span className="text-sm font-medium">{feedback.message}</span>
              </div>
            )}

            <form
              onSubmit={handleRegisterEvent}
              className="bg-slate-800/50 border border-slate-700/80 rounded-2xl p-6 shadow-xl space-y-6"
            >
              <div>
                <label className="block text-xs font-semibold text-slate-300 mb-2">1. Seleccione Colaborador / Código ID</label>
                <select
                  value={selectedEmp}
                  onChange={e => setSelectedEmp(e.target.value)}
                  className="w-full bg-slate-950 border border-slate-700 rounded-xl px-4 py-3.5 text-sm text-slate-100 focus:outline-none focus:border-blue-500"
                >
                  <option value="">-- Buscar por Nombre o Código ID --</option>
                  {employees
                    .filter(e => e.active !== false)
                    .map(emp => {
                      const st = employeeStatusMap[emp.id]?.state || 'AUSENTE';
                      const comp = companies.find(c => c.id === emp.companyId);
                      return (
                        <option key={emp.id} value={emp.id}>
                          [{emp.id}] {emp.name} — {comp ? comp.name : ''} ({st})
                        </option>
                      );
                    })}
                </select>
              </div>

              <div>
                <label className="block text-xs font-semibold text-slate-300 mb-2">2. Tipo de Movimiento</label>
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                  {[
                    { id: 'CHECK_IN', title: 'Entrada', sub: 'Check-In', Icon: LogIn },
                    { id: 'LUNCH_START', title: 'Salida Almuerzo', sub: 'Inicio Receso', Icon: Utensils },
                    { id: 'LUNCH_END', title: 'Regreso Almuerzo', sub: 'Fin Receso', Icon: RotateCcw },
                    { id: 'CHECK_OUT', title: 'Salida', sub: 'Check-Out', Icon: LogOut }
                  ].map(item => {
                    const IconComp = item.Icon;
                    const isSelected = selectedAction === item.id;
                    return (
                      <button
                        type="button"
                        key={item.id}
                        onClick={() => setSelectedAction(item.id)}
                        className={`p-3.5 rounded-xl border text-left transition-all flex flex-col justify-between ${
                          isSelected
                            ? 'border-blue-500 bg-blue-600/20 text-white ring-1 ring-blue-500'
                            : 'border-slate-700/80 bg-slate-950/60 text-slate-400 hover:border-slate-600'
                        }`}
                      >
                        <IconComp className={`w-5 h-5 mb-2 ${isSelected ? 'text-blue-400' : 'text-slate-400'}`} />
                        <div>
                          <div className="text-xs font-bold text-slate-100">{item.title}</div>
                          <div className="text-[10px] text-slate-400">{item.sub}</div>
                        </div>
                      </button>
                    );
                  })}
                </div>
              </div>

              <div>
                <label className="block text-xs font-semibold text-slate-300 mb-2">3. Estación de Marcaje / Ubicación</label>
                <select
                  value={selectedStation}
                  onChange={e => setSelectedStation(e.target.value)}
                  className="w-full bg-slate-950 border border-slate-700 rounded-xl px-4 py-3 text-sm text-slate-200 focus:outline-none focus:border-blue-500"
                >
                  {stations
                    .filter(s => s.active)
                    .map(st => (
                      <option key={st.id} value={st.id}>
                        {st.name} ({st.id})
                      </option>
                    ))}
                </select>
              </div>

              <div>
                <label className="block text-xs font-semibold text-slate-300 mb-2">
                  4. Observaciones / Justificación de Incidencia (Opcional)
                </label>
                <textarea
                  rows="2"
                  placeholder="Escriba aquí notas de llegadas tardías, permisos especiales u observaciones..."
                  value={incidentNote}
                  onChange={e => setIncidentNote(e.target.value)}
                  className="w-full bg-slate-950 border border-slate-700 rounded-xl px-4 py-2.5 text-sm text-slate-200 focus:outline-none focus:border-blue-500"
                />
              </div>

              <button
                type="submit"
                className="w-full bg-gradient-to-r from-blue-600 to-cyan-600 hover:from-blue-500 hover:to-cyan-500 text-white font-semibold py-3.5 rounded-xl transition-all shadow-lg flex items-center justify-center space-x-2"
              >
                <UserCheck className="w-5 h-5" />
                <span>Confirmar Registro de Asistencia</span>
              </button>
            </form>
          </div>
        )}

        {viewMode === 'TOWER' && isTowerUnlocked && (
          <div className="space-y-6">
            <div className="bg-indigo-950/40 border border-indigo-500/30 rounded-xl px-4 py-2.5 flex flex-wrap items-center justify-between gap-2 text-xs">
              <div className="flex items-center space-x-2 text-indigo-300">
                <ShieldAlert className="w-4 h-4 text-indigo-400" />
                <span>
                  Sesión de Supervisión Activa: <strong className="text-white">{activeSupervisor?.name}</strong>
                </span>
              </div>
              <div className="text-slate-400 font-mono">
                Hora Miami: <span className="text-cyan-400 font-bold">{getMiamiTime(currentTime)}</span>
              </div>
            </div>

            <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
              <div className="bg-slate-800/60 border border-slate-700/70 p-4 rounded-xl">
                <div className="text-xs text-slate-400">Plantilla Activa</div>
                <div className="text-2xl font-bold text-white mt-1">{kpis.total}</div>
              </div>
              <div className="bg-emerald-950/20 border border-emerald-500/30 p-4 rounded-xl">
                <div className="text-xs text-emerald-400">Presentes / En Turno</div>
                <div className="text-2xl font-bold text-emerald-300 mt-1">{kpis.presentes}</div>
              </div>
              <div className="bg-amber-950/20 border border-amber-500/30 p-4 rounded-xl">
                <div className="text-xs text-amber-400">En Almuerzo</div>
                <div className="text-2xl font-bold text-amber-300 mt-1">{kpis.almuerzo}</div>
              </div>
              <div className="bg-indigo-950/20 border border-indigo-500/30 p-4 rounded-xl">
                <div className="text-xs text-indigo-400">Jornada Finalizada</div>
                <div className="text-2xl font-bold text-indigo-300 mt-1">{kpis.finalizados}</div>
              </div>
              <div className="bg-slate-800/40 border border-slate-700/40 p-4 rounded-xl">
                <div className="text-xs text-slate-400">Sin Registrar</div>
                <div className="text-2xl font-bold text-slate-400 mt-1">{kpis.ausentes}</div>
              </div>
            </div>

            <div className="flex flex-wrap border-b border-slate-800 gap-6">
              <button
                type="button"
                onClick={() => setActiveTab('CONSOLA')}
                className={`pb-3 text-sm font-semibold border-b-2 flex items-center space-x-2 transition-all ${
                  activeTab === 'CONSOLA'
                    ? 'border-indigo-500 text-indigo-400'
                    : 'border-transparent text-slate-400 hover:text-slate-200'
                }`}
              >
                <Table className="w-4 h-4" />
                <span>Consola Operativa</span>
              </button>
              <button
                type="button"
                onClick={() => setActiveTab('AUDITORIA')}
                className={`pb-3 text-sm font-semibold border-b-2 flex items-center space-x-2 transition-all ${
                  activeTab === 'AUDITORIA'
                    ? 'border-indigo-500 text-indigo-400'
                    : 'border-transparent text-slate-400 hover:text-slate-200'
                }`}
              >
                <ListChecks className="w-4 h-4" />
                <span>Auditoría de Marcajes</span>
              </button>
              <button
                type="button"
                onClick={() => setActiveTab('CONFIG')}
                className={`pb-3 text-sm font-semibold border-b-2 flex items-center space-x-2 transition-all ${
                  activeTab === 'CONFIG'
                    ? 'border-indigo-500 text-indigo-400'
                    : 'border-transparent text-slate-400 hover:text-slate-200'
                }`}
              >
                <Sliders className="w-4 h-4" />
                <span>Configuración (Empresas, Estaciones y Personal)</span>
              </button>
            </div>

            {activeTab === 'CONSOLA' && (
              <div className="space-y-4">
                <div className="grid grid-cols-1 md:grid-cols-3 gap-3 bg-slate-800/40 p-3.5 rounded-xl border border-slate-700/60">
                  <div className="relative">
                    <Search className="w-4 h-4 text-slate-500 absolute left-3 top-3" />
                    <input
                      type="text"
                      placeholder="Buscar por nombre o ID..."
                      value={searchTerm}
                      onChange={e => setSearchTerm(e.target.value)}
                      className="w-full bg-slate-900 border border-slate-700 rounded-lg pl-9 pr-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-indigo-500"
                    />
                  </div>
                  <select
                    value={selectedCompanyFilter}
                    onChange={e => setSelectedCompanyFilter(e.target.value)}
                    className="bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none"
                  >
                    <option value="ALL">Todas las Empresas</option>
                    {companies.map(c => (
                      <option key={c.id} value={c.id}>
                        {c.name}
                      </option>
                    ))}
                  </select>
                  <select
                    value={selectedDeptFilter}
                    onChange={e => setSelectedDeptFilter(e.target.value)}
                    className="bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none"
                  >
                    <option value="ALL">Todos los Departamentos</option>
                    {allDepartmentsList.map(d => (
                      <option key={d} value={d}>
                        {d}
                      </option>
                    ))}
                  </select>
                </div>

                <div className="bg-slate-800/50 border border-slate-700/80 rounded-2xl overflow-hidden shadow-xl">
                  <div className="overflow-x-auto">
                    <table className="w-full text-left text-sm">
                      <thead>
                        <tr className="bg-slate-900/80 border-b border-slate-700/80 text-xs font-semibold uppercase text-slate-400">
                          <th className="py-3.5 px-4">Colaborador</th>
                          <th className="py-3.5 px-4">Empresa</th>
                          <th className="py-3.5 px-4">Departamento / Cargo</th>
                          <th className="py-3.5 px-4">Estado</th>
                          <th className="py-3.5 px-4">Último Marcaje</th>
                          <th className="py-3.5 px-4 text-right">Acción</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-slate-700/50 text-slate-300">
                        {filteredEmployees.map(emp => {
                          const st = employeeStatusMap[emp.id]?.state || 'AUSENTE';
                          const comp = companies.find(c => c.id === emp.companyId);
                          const isActive = emp.active !== false;

                          return (
                            <tr key={emp.id} className="hover:bg-slate-700/30">
                              <td className="py-3.5 px-4">
                                <div className="flex items-center space-x-2">
                                  <span className={`font-semibold ${isActive ? 'text-slate-100' : 'text-slate-500 line-through'}`}>
                                    {emp.name}
                                  </span>
                                  {emp.isSupervisor && (
                                    <span className="px-1.5 py-0.5 text-[10px] rounded bg-indigo-950 text-indigo-300 border border-indigo-500/40">
                                      Admin
                                    </span>
                                  )}
                                </div>
                                <div className="text-xs font-mono text-slate-500">{emp.id}</div>
                              </td>
                              <td className="py-3.5 px-4">
                                <span className="text-xs font-medium text-blue-300 bg-blue-950/60 border border-blue-500/30 px-2.5 py-1 rounded-lg">
                                  {comp ? comp.name : 'Sin Empresa'}
                                </span>
                              </td>
                              <td className="py-3.5 px-4">
                                <div className="text-xs font-medium text-slate-200">{emp.department}</div>
                                <div className="text-xs text-slate-400">{emp.role}</div>
                              </td>
                              <td className="py-3.5 px-4">
                                {!isActive ? (
                                  <span className="px-2.5 py-1 rounded-full text-xs font-medium bg-amber-950/50 text-amber-400 border border-amber-500/40">
                                    INACTIVO
                                  </span>
                                ) : (
                                  <span
                                    className={`px-2.5 py-1 rounded-full text-xs font-medium ${
                                      st === 'PRESENTE'
                                        ? 'bg-emerald-950 text-emerald-300 border border-emerald-500/40'
                                        : st === 'ALMUERZO'
                                        ? 'bg-amber-950 text-amber-300 border border-amber-500/40'
                                        : st === 'FINALIZADO'
                                        ? 'bg-indigo-950 text-indigo-300 border border-indigo-500/40'
                                        : 'bg-slate-900 text-slate-400 border border-slate-700'
                                    }`}
                                  >
                                    {st}
                                  </span>
                                )}
                              </td>
                              <td className="py-3.5 px-4 font-mono text-xs">
                                {employeeStatusMap[emp.id]?.lastAction || '--:--:--'}
                              </td>
                              <td className="py-3.5 px-4 text-right">
                                {isActive && (st === 'PRESENTE' || st === 'ALMUERZO') && (
                                  <button
                                    type="button"
                                    onClick={() => handleForceClockOut(emp.id)}
                                    className="px-3 py-1.5 bg-rose-950/60 hover:bg-rose-900 border border-rose-500/50 text-rose-300 rounded-lg text-xs font-medium"
                                  >
                                    Forzar Salida
                                  </button>
                                )}
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                </div>
              </div>
            )}

            {activeTab === 'AUDITORIA' && (
              <div className="space-y-4">
                <div className="flex justify-between items-center bg-slate-800/40 p-3.5 rounded-xl border border-slate-700/60">
                  <div className="text-xs text-slate-400">
                    Total Eventos: <span className="font-bold text-slate-200">{auditLog.length}</span>
                  </div>
                  <button
                    type="button"
                    onClick={exportCSV}
                    className="px-4 py-2 bg-emerald-600 hover:bg-emerald-500 text-white rounded-lg text-xs font-semibold flex items-center space-x-2"
                  >
                    <Download className="w-4 h-4" />
                    <span>Exportar CSV</span>
                  </button>
                </div>

                <div className="bg-slate-800/50 border border-slate-700/80 rounded-2xl overflow-hidden shadow-xl">
                  <div className="overflow-x-auto">
                    <table className="w-full text-left text-sm">
                      <thead>
                        <tr className="bg-slate-900/80 border-b border-slate-700/80 text-xs font-semibold uppercase text-slate-400">
                          <th className="py-3.5 px-4">Hora Miami / Fecha</th>
                          <th className="py-3.5 px-4">Colaborador</th>
                          <th className="py-3.5 px-4">Empresa / Depto</th>
                          <th className="py-3.5 px-4">Evento</th>
                          <th className="py-3.5 px-4">Estación</th>
                          <th className="py-3.5 px-4">Observaciones</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-slate-700/50 text-slate-300 text-xs">
                        {auditLog.map(log => (
                          <tr key={log.id} className="hover:bg-slate-700/30">
                            <td className="py-3 px-4 font-mono text-cyan-400">
                              {log.timestamp}
                              <span className="text-[10px] text-slate-500 block">{log.date}</span>
                            </td>
                            <td className="py-3 px-4 font-medium text-slate-200">
                              {log.empName} <span className="text-slate-500 font-mono">({log.empId})</span>
                            </td>
                            <td className="py-3 px-4">
                              <div className="text-blue-300 font-medium">{log.companyName}</div>
                              <div className="text-[11px] text-slate-400">{log.dept}</div>
                            </td>
                            <td className="py-3 px-4">
                              <span className="px-2 py-1 rounded bg-slate-900 border border-slate-700 text-slate-200">
                                {log.actionLabel}
                              </span>
                            </td>
                            <td className="py-3 px-4 text-slate-300">{log.station}</td>
                            <td className="py-3 px-4 italic text-slate-400">{log.note}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              </div>
            )}

            {activeTab === 'CONFIG' && (
              <div className="space-y-6">
                <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
                  <div className="lg:col-span-2 bg-slate-800/60 border border-emerald-500/30 rounded-2xl p-5 space-y-4">
                    <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-700/80 pb-3">
                      <div className="flex items-center space-x-2.5">
                        <div className="p-2 rounded-lg bg-emerald-500/20 text-emerald-400 border border-emerald-500/30">
                          <FileSpreadsheet className="w-5 h-5" />
                        </div>
                        <div>
                          <h3 className="text-sm font-bold text-slate-100">Trazabilidad con Google Sheets</h3>
                          <p className="text-[11px] text-slate-400">Acceso directo a su hoja de cálculo y webhook.</p>
                        </div>
                      </div>
                      <a
                        href={googleSheetDocUrl.trim() ? googleSheetDocUrl.trim() : 'https://docs.google.com/spreadsheets/u/0/'}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="px-4 py-2 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white text-xs font-semibold flex items-center space-x-2 shadow-md"
                      >
                        <ExternalLink className="w-4 h-4" />
                        <span>Abrir Google Sheet</span>
                      </a>
                    </div>

                    <div className="grid grid-cols-1 md:grid-cols-2 gap-2.5">
                      <div>
                        <label className="block text-[10px] font-semibold uppercase text-slate-400 mb-1">
                          Enlace del Google Sheet
                        </label>
                        <input
                          type="url"
                          placeholder="https://docs.google.com/spreadsheets/d/..."
                          value={googleSheetDocUrl}
                          onChange={e => setGoogleSheetDocUrl(e.target.value)}
                          className="w-full bg-slate-950 border border-slate-700 rounded-xl px-3.5 py-2 text-xs text-slate-200 focus:outline-none focus:border-emerald-500"
                        />
                      </div>
                      <div>
                        <label className="block text-[10px] font-semibold uppercase text-slate-400 mb-1">
                          URL Webhook Apps Script
                        </label>
                        <div className="flex gap-2">
                          <input
                            type="url"
                            placeholder="https://script.google.com/macros/s/.../exec"
                            value={googleSheetUrl}
                            onChange={e => setGoogleSheetUrl(e.target.value)}
                            className="flex-1 bg-slate-950 border border-slate-700 rounded-xl px-3.5 py-2 text-xs text-slate-200 focus:outline-none focus:border-emerald-500"
                          />
                          <button
                            type="button"
                            onClick={handleFullSyncToSheets}
                            disabled={!googleSheetUrl}
                            className={`px-3 py-2 rounded-xl text-xs font-semibold flex items-center space-x-1 shrink-0 ${
                              googleSheetUrl ? 'bg-emerald-600 hover:bg-emerald-500 text-white' : 'bg-slate-800 text-slate-500 cursor-not-allowed'
                            }`}
                          >
                            <RefreshCw className="w-3.5 h-3.5" />
                            <span>Sincronizar</span>
                          </button>
                        </div>
                      </div>
                    </div>
                  </div>

                  <div className="bg-slate-800/60 border border-indigo-500/30 rounded-2xl p-5 flex flex-col justify-between space-y-4">
                    <div className="flex items-center space-x-2.5 border-b border-slate-700/80 pb-3">
                      <div className="p-2 rounded-lg bg-indigo-500/20 text-indigo-400 border border-indigo-500/30">
                        <ShieldCheck className="w-5 h-5" />
                      </div>
                      <div>
                        <h3 className="text-sm font-bold text-slate-100">Seguridad Torre (OTP)</h3>
                        <p className="text-[11px] text-slate-400">
                          Estado:{' '}
                          {isOtpUsed ? (
                            <span className="text-emerald-400 font-semibold">Personalizada Activa</span>
                          ) : (
                            <span className="text-amber-400 font-semibold">OTP Pendiente (admin123)</span>
                          )}
                        </p>
                      </div>
                    </div>
                    <button
                      type="button"
                      onClick={() => {
                        setNewPasswordInput('');
                        setConfirmPasswordInput('');
                        setPasswordChangeError('');
                        setForcePasswordChangeModal(true);
                      }}
                      className="w-full py-2 px-3 rounded-xl bg-indigo-600 hover:bg-indigo-500 text-white text-xs font-semibold flex items-center justify-center space-x-1.5"
                    >
                      <KeyRound className="w-3.5 h-3.5" />
                      <span>Cambiar Contraseña Ahora</span>
                    </button>
                  </div>
                </div>

                <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                  <div className="bg-slate-800/60 border border-slate-700/80 rounded-2xl p-6 space-y-5">
                    <div className="flex items-center space-x-2 border-b border-slate-700 pb-3">
                      <Users className="w-5 h-5 text-indigo-400" />
                      <h3 className="text-base font-bold text-slate-100">Gestión de Colaboradores y Permisos</h3>
                    </div>

                    <form
                      onSubmit={handleAddEmployee}
                      className="space-y-3 bg-slate-900/70 p-4 rounded-xl border border-slate-700/60"
                    >
                      <div className="grid grid-cols-2 gap-3">
                        <div>
                          <label className="block text-[11px] text-slate-400 mb-1">Código / ID</label>
                          <input
                            type="text"
                            placeholder="Ej. EMP-1006"
                            value={newEmpId}
                            onChange={e => setNewEmpId(e.target.value)}
                            className="w-full bg-slate-950 border border-slate-700 rounded-lg px-3 py-2 text-xs text-slate-200"
                            required
                          />
                        </div>
                        <div>
                          <label className="block text-[11px] text-slate-400 mb-1">Nombre Completo</label>
                          <input
                            type="text"
                            placeholder="Ej. Javier Torres"
                            value={newEmpName}
                            onChange={e => setNewEmpName(e.target.value)}
                            className="w-full bg-slate-950 border border-slate-700 rounded-lg px-3 py-2 text-xs text-slate-200"
                            required
                          />
                        </div>
                      </div>
                      <div className="grid grid-cols-2 gap-3">
                        <div>
                          <label className="block text-[11px] text-slate-400 mb-1">Empresa</label>
                          <select
                            value={newEmpCompany}
                            onChange={e => setNewEmpCompany(e.target.value)}
                            className="w-full bg-slate-950 border border-slate-700 rounded-lg px-3 py-2 text-xs text-slate-200"
                          >
                            {companies
                              .filter(c => c.active !== false)
                              .map(c => (
                                <option key={c.id} value={c.id}>
                                  {c.name}
                                </option>
                              ))}
                          </select>
                        </div>
                        <div>
                          <label className="block text-[11px] text-slate-400 mb-1">Departamento</label>
                          <select
                            value={newEmpDeptSelect}
                            onChange={e => setNewEmpDeptSelect(e.target.value)}
                            className="w-full bg-slate-950 border border-slate-700 rounded-lg px-3 py-2 text-xs text-slate-200"
                          >
                            {DEFAULT_DEPARTMENTS.map(d => (
                              <option key={d} value={d}>
                                {d === 'OTRO_PERSONALIZADO' ? '+ Personalizar...' : d}
                              </option>
                            ))}
                          </select>
                        </div>
                      </div>
                      {newEmpDeptSelect === 'OTRO_PERSONALIZADO' && (
                        <div>
                          <input
                            type="text"
                            placeholder="Nombre del Departamento..."
                            value={customDeptInput}
                            onChange={e => setCustomDeptInput(e.target.value)}
                            className="w-full bg-slate-950 border border-indigo-500/60 rounded-lg px-3 py-2 text-xs text-slate-100"
                            required
                          />
                        </div>
                      )}
                      <div>
                        <label className="block text-[11px] text-slate-400 mb-1">Cargo / Puesto</label>
                        <input
                          type="text"
                          placeholder="Cargo..."
                          value={newEmpRole}
                          onChange={e => setNewEmpRole(e.target.value)}
                          className="w-full bg-slate-950 border border-slate-700 rounded-lg px-3 py-2 text-xs text-slate-200"
                          required
                        />
                      </div>
                      <div className="flex items-center space-x-2 pt-1">
                        <input
                          type="checkbox"
                          id="isSupervisorCheck"
                          checked={newEmpIsSupervisor}
                          onChange={e => setNewEmpIsSupervisor(e.target.checked)}
                          className="rounded border-slate-700 bg-slate-900 text-indigo-600"
                        />
                        <label htmlFor="isSupervisorCheck" className="text-xs text-slate-300">
                          Acceso administrativo a la Torre de Control
                        </label>
                      </div>
                      <button
                        type="submit"
                        className="w-full bg-indigo-600 hover:bg-indigo-500 text-white py-2.5 rounded-lg text-xs font-semibold flex items-center justify-center space-x-1.5"
                      >
                        <Plus className="w-4 h-4" />
                        <span>Agregar Colaborador</span>
                      </button>
                    </form>

                    <div className="max-h-72 overflow-y-auto space-y-2 pr-1">
                      {employees.map(emp => {
                        const isActive = emp.active !== false;
                        const comp = companies.find(c => c.id === emp.companyId);
                        return (
                          <div
                            key={emp.id}
                            className="bg-slate-900/90 p-3 rounded-xl border border-slate-700/70 flex flex-col sm:flex-row sm:items-center justify-between gap-2 text-xs"
                          >
                            <div>
                              <div className="flex items-center space-x-2">
                                <span className={`font-semibold ${isActive ? 'text-slate-100' : 'text-slate-500 line-through'}`}>
                                  {emp.name}
                                </span>
                                <span className="text-slate-500 font-mono">({emp.id})</span>
                                {emp.isSupervisor && (
                                  <span className="px-1.5 py-0.5 rounded bg-indigo-950 text-indigo-300 text-[10px]">
                                    Admin
                                  </span>
                                )}
                              </div>
                              <div className="text-[11px] text-blue-400 mt-0.5">
                                {comp ? comp.name : 'Sin Empresa'} • {emp.department}
                              </div>
                            </div>
                            <div className="flex flex-wrap items-center gap-1.5 shrink-0">
                              <button
                                type="button"
                                onClick={() => openEditRecord('EMPLOYEE', emp)}
                                className="px-2.5 py-1 rounded bg-blue-950 text-blue-300 border border-blue-500/40"
                              >
                                <Edit3 className="w-3.5 h-3.5" />
                              </button>
                              <button
                                type="button"
                                onClick={() => handleToggleEmployee(emp.id)}
                                className={`px-2.5 py-1 rounded ${
                                  isActive ? 'bg-emerald-950 text-emerald-300' : 'bg-amber-950 text-amber-300'
                                }`}
                              >
                                {isActive ? 'Activo' : 'Inactivo'}
                              </button>
                              <button
                                type="button"
                                onClick={() => requestDelete('EMPLOYEE', emp.id, emp.name)}
                                className="px-2.5 py-1 rounded bg-rose-950 text-rose-300"
                              >
                                <Trash2 className="w-3.5 h-3.5" />
                              </button>
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  </div>

                  <div className="space-y-6">
                    <div className="bg-slate-800/60 border border-slate-700/80 rounded-2xl p-6 space-y-4">
                      <div className="flex items-center space-x-2 border-b border-slate-700 pb-3">
                        <Briefcase className="w-5 h-5 text-cyan-400" />
                        <h3 className="text-base font-bold text-slate-100">Consola de Empresas</h3>
                      </div>
                      <form
                        onSubmit={handleAddCompany}
                        className="space-y-3 bg-slate-900/70 p-4 rounded-xl border border-slate-700/60"
                      >
                        <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
                          <input
                            type="text"
                            placeholder="Código (COMP-03)"
                            value={newCompanyId}
                            onChange={e => setNewCompanyId(e.target.value)}
                            className="bg-slate-950 border border-slate-700 rounded-lg px-3 py-2 text-xs text-slate-200"
                            required
                          />
                          <input
                            type="text"
                            placeholder="Nombre empresa..."
                            value={newCompanyName}
                            onChange={e => setNewCompanyName(e.target.value)}
                            className="sm:col-span-2 bg-slate-950 border border-slate-700 rounded-lg px-3 py-2 text-xs text-slate-200"
                            required
                          />
                        </div>
                        <button
                          type="submit"
                          className="w-full bg-cyan-600 hover:bg-cyan-500 text-white py-2 rounded-lg text-xs font-semibold flex items-center justify-center space-x-1.5"
                        >
                          <Plus className="w-4 h-4" />
                          <span>Asignar Empresa</span>
                        </button>
                      </form>
                      <div className="max-h-44 overflow-y-auto space-y-2 pr-1">
                        {companies.map(comp => (
                          <div
                            key={comp.id}
                            className="bg-slate-900/90 p-3 rounded-xl border border-slate-700/70 flex justify-between items-center text-xs"
                          >
                            <div>
                              <span className={comp.active ? 'text-slate-100' : 'text-slate-500 line-through'}>
                                {comp.name}
                              </span>{' '}
                              <span className="text-slate-500 font-mono">({comp.id})</span>
                            </div>
                            <div className="flex items-center space-x-1.5">
                              <button
                                type="button"
                                onClick={() => openEditRecord('COMPANY', comp)}
                                className="px-2.5 py-1 rounded bg-blue-950 text-blue-300"
                              >
                                <Edit3 className="w-3.5 h-3.5" />
                              </button>
                              <button
                                type="button"
                                onClick={() => handleToggleCompany(comp.id)}
                                className={`px-2 py-1 rounded ${
                                  comp.active ? 'bg-emerald-950 text-emerald-300' : 'bg-slate-800 text-slate-500'
                                }`}
                              >
                                {comp.active ? 'Activa' : 'Inactiva'}
                              </button>
                              <button
                                type="button"
                                onClick={() => requestDelete('COMPANY', comp.id, comp.name)}
                                className="px-2 py-1 rounded bg-rose-950 text-rose-300"
                              >
                                <Trash2 className="w-3.5 h-3.5" />
                              </button>
                            </div>
                          </div>
                        ))}
                      </div>
                    </div>

                    <div className="bg-slate-800/60 border border-slate-700/80 rounded-2xl p-6 space-y-4">
                      <div className="flex items-center space-x-2 border-b border-slate-700 pb-3">
                        <MapPin className="w-5 h-5 text-blue-400" />
                        <h3 className="text-base font-bold text-slate-100">Estaciones de Marcaje</h3>
                      </div>
                      <form
                        onSubmit={handleAddStation}
                        className="space-y-3 bg-slate-900/70 p-4 rounded-xl border border-slate-700/60"
                      >
                        <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
                          <input
                            type="text"
                            placeholder="ID (ST-04)"
                            value={newStationId}
                            onChange={e => setNewStationId(e.target.value)}
                            className="bg-slate-950 border border-slate-700 rounded-lg px-3 py-2 text-xs text-slate-200"
                            required
                          />
                          <input
                            type="text"
                            placeholder="Nombre Sede"
                            value={newStationName}
                            onChange={e => setNewStationName(e.target.value)}
                            className="sm:col-span-2 bg-slate-950 border border-slate-700 rounded-lg px-3 py-2 text-xs text-slate-200"
                            required
                          />
                        </div>
                        <button
                          type="submit"
                          className="w-full bg-blue-600 hover:bg-blue-500 text-white py-2 rounded-lg text-xs font-semibold flex items-center justify-center space-x-1.5"
                        >
                          <Plus className="w-4 h-4" />
                          <span>Crear Estación</span>
                        </button>
                      </form>
                      <div className="max-h-44 overflow-y-auto space-y-2 pr-1">
                        {stations.map(st => (
                          <div
                            key={st.id}
                            className="bg-slate-900/90 p-3 rounded-xl border border-slate-700/70 flex justify-between items-center text-xs"
                          >
                            <div>
                              <span className={st.active ? 'text-slate-100' : 'text-slate-500 line-through'}>
                                {st.name}
                              </span>{' '}
                              <span className="text-slate-500 font-mono">({st.id})</span>
                            </div>
                            <div className="flex items-center space-x-1.5">
                              <button
                                type="button"
                                onClick={() => openEditRecord('STATION', st)}
                                className="px-2.5 py-1 rounded bg-blue-950 text-blue-300"
                              >
                                <Edit3 className="w-3.5 h-3.5" />
                              </button>
                              <button
                                type="button"
                                onClick={() => handleToggleStation(st.id)}
                                className={`px-2 py-1 rounded ${
                                  st.active ? 'bg-emerald-950 text-emerald-300' : 'bg-slate-800 text-slate-500'
                                }`}
                              >
                                {st.active ? 'Activa' : 'Inactiva'}
                              </button>
                              <button
                                type="button"
                                onClick={() => requestDelete('STATION', st.id, st.name)}
                                className="px-2 py-1 rounded bg-rose-950 text-rose-300"
                              >
                                <Trash2 className="w-3.5 h-3.5" />
                              </button>
                            </div>
                          </div>
                        ))}
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            )}
          </div>
        )}
      </main>

      {authModalOpen && (
        <div className="fixed inset-0 z-50 bg-slate-950/80 backdrop-blur-sm flex items-center justify-center p-4">
          <div className="bg-slate-900 border border-slate-700 rounded-2xl max-w-md w-full p-6 shadow-2xl space-y-5">
            <div className="flex items-center justify-between border-b border-slate-800 pb-3">
              <div className="flex items-center space-x-2.5">
                <div className="p-2 rounded-lg bg-indigo-600/20 text-indigo-400 border border-indigo-500/30">
                  <KeyRound className="w-5 h-5" />
                </div>
                <div>
                  <h3 className="text-base font-bold text-white">Acceso a Torre de Control</h3>
                  <p className="text-xs text-slate-400">Verificación de Personal Autorizado</p>
                </div>
              </div>
              <button type="button" onClick={() => setAuthModalOpen(false)} className="text-slate-400 hover:text-white">
                <X className="w-5 h-5" />
              </button>
            </div>
            {authError && (
              <div className="p-3 rounded-xl bg-rose-950/70 border border-rose-500/50 text-rose-300 text-xs">
                {authError}
              </div>
            )}
            <form onSubmit={handleConfirmTowerAuth} className="space-y-4">
              <div>
                <label className="block text-xs font-medium text-slate-300 mb-1.5">Supervisor Autorizado</label>
                <select
                  value={authSelectedUser}
                  onChange={e => setAuthSelectedUser(e.target.value)}
                  className="w-full bg-slate-950 border border-slate-700 rounded-xl px-3.5 py-2.5 text-sm text-slate-100"
                  required
                >
                  <option value="">-- Seleccione Supervisor --</option>
                  {employees
                    .filter(e => e.isSupervisor && e.active !== false)
                    .map(sup => (
                      <option key={sup.id} value={sup.id}>
                        [{sup.id}] {sup.name}
                      </option>
                    ))}
                </select>
              </div>
              <div>
                <label className="block text-xs font-medium text-slate-300 mb-1.5">
                  {isOtpUsed ? 'Contraseña' : 'Clave OTP Temporal'}
                </label>
                <input
                  type="password"
                  placeholder={isOtpUsed ? 'Contraseña privada' : 'Clave OTP: admin123'}
                  value={authPassword}
                  onChange={e => setAuthPassword(e.target.value)}
                  className="w-full bg-slate-950 border border-slate-700 rounded-xl px-3.5 py-2.5 text-sm text-slate-100"
                  required
                />
              </div>
              <div className="flex justify-end space-x-3 pt-2">
                <button
                  type="button"
                  onClick={() => setAuthModalOpen(false)}
                  className="px-4 py-2 rounded-xl bg-slate-800 text-slate-300 text-xs"
                >
                  Cancelar
                </button>
                <button type="submit" className="px-5 py-2 rounded-xl bg-indigo-600 text-white text-xs font-semibold">
                  Ingresar
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {inactiveModal.open && (
        <div className="fixed inset-0 z-50 bg-slate-950/80 backdrop-blur-sm flex items-center justify-center p-4">
          <div className="bg-slate-900 border border-slate-700 rounded-2xl max-w-md w-full p-6 shadow-2xl space-y-4">
            <h3 className="text-base font-bold text-amber-400">Pasar a Inactivo: {inactiveModal.empName}</h3>
            <form onSubmit={handleConfirmInactivation} className="space-y-4">
              <textarea
                rows="3"
                placeholder="Motivo (Vacaciones, Reposo...)"
                value={inactiveModal.reason}
                onChange={e => setInactiveModal(prev => ({ ...prev, reason: e.target.value }))}
                className="w-full bg-slate-950 border border-slate-700 rounded-xl p-3 text-sm text-slate-100"
                required
              />
              <div className="flex justify-end space-x-3">
                <button
                  type="button"
                  onClick={() => setInactiveModal({ open: false, empId: null, empName: '', reason: '' })}
                  className="px-4 py-2 bg-slate-800 text-slate-300 text-xs rounded-xl"
                >
                  Cancelar
                </button>
                <button type="submit" className="px-4 py-2 bg-amber-600 text-white text-xs rounded-xl font-semibold">
                  Confirmar
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {deleteModal.open && (
        <div className="fixed inset-0 z-50 bg-slate-950/80 backdrop-blur-sm flex items-center justify-center p-4">
          <div className="bg-slate-900 border border-slate-700 rounded-2xl max-w-md w-full p-6 shadow-2xl space-y-4">
            <h3 className="text-base font-bold text-rose-400">Confirmar Eliminación</h3>
            <p className="text-xs text-slate-300">¿Desea eliminar a "{deleteModal.name}"?</p>
            <div className="flex justify-end space-x-3">
              <button
                type="button"
                onClick={() => setDeleteModal({ open: false, type: null, id: null, name: '' })}
                className="px-4 py-2 bg-slate-800 text-slate-300 text-xs rounded-xl"
              >
                Cancelar
              </button>
              <button
                type="button"
                onClick={confirmDeleteAction}
                className="px-4 py-2 bg-rose-600 text-white text-xs rounded-xl font-semibold"
              >
                Eliminar
              </button>
            </div>
          </div>
        </div>
      )}

      {forcePasswordChangeModal && (
        <div className="fixed inset-0 z-50 bg-slate-950/90 backdrop-blur-md flex items-center justify-center p-4">
          <div className="bg-slate-900 border border-indigo-500/50 rounded-2xl max-w-md w-full p-6 shadow-2xl space-y-5">
            <h3 className="text-base font-bold text-white">Definir Nueva Contraseña de Torre</h3>
            {passwordChangeError && (
              <div className="p-3 rounded-xl bg-rose-950 text-rose-300 text-xs">{passwordChangeError}</div>
            )}
            <form onSubmit={handleSaveNewPassword} className="space-y-4">
              <input
                type="password"
                placeholder="Nueva contraseña privada"
                value={newPasswordInput}
                onChange={e => setNewPasswordInput(e.target.value)}
                className="w-full bg-slate-950 border border-slate-700 rounded-xl p-3 text-sm text-slate-100"
                required
              />
              <input
                type="password"
                placeholder="Confirmar contraseña"
                value={confirmPasswordInput}
                onChange={e => setConfirmPasswordInput(e.target.value)}
                className="w-full bg-slate-950 border border-slate-700 rounded-xl p-3 text-sm text-slate-100"
                required
              />
              <div className="flex justify-end space-x-3">
                <button type="submit" className="px-5 py-2.5 bg-indigo-600 text-white text-xs font-semibold rounded-xl">
                  Guardar
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {editModal.open && (
        <div className="fixed inset-0 z-50 bg-slate-950/80 backdrop-blur-sm flex items-center justify-center p-4">
          <div className="bg-slate-900 border border-slate-700 rounded-2xl max-w-md w-full p-6 shadow-2xl space-y-4">
            <h3 className="text-base font-bold text-white">Editar Registro</h3>
            <form onSubmit={handleSaveEditModal} className="space-y-3">
              <input
                type="text"
                value={editModal.id}
                onChange={e => setEditModal(prev => ({ ...prev, id: e.target.value }))}
                className="w-full bg-slate-950 border border-slate-700 rounded p-2 text-xs text-slate-100"
                required
              />
              <input
                type="text"
                value={editModal.name}
                onChange={e => setEditModal(prev => ({ ...prev, name: e.target.value }))}
                className="w-full bg-slate-950 border border-slate-700 rounded p-2 text-xs text-slate-100"
                required
              />
              {editModal.type === 'EMPLOYEE' && (
                <select
                  value={editModal.companyId}
                  onChange={e => setEditModal(prev => ({ ...prev, companyId: e.target.value }))}
                  className="w-full bg-slate-950 border border-slate-700 rounded p-2 text-xs text-slate-100"
                >
                  {companies.map(c => (
                    <option key={c.id} value={c.id}>
                      {c.name}
                    </option>
                  ))}
                </select>
              )}
              <div className="flex justify-end space-x-3 pt-2">
                <button
                  type="button"
                  onClick={() => setEditModal(prev => ({ ...prev, open: false }))}
                  className="px-4 py-2 bg-slate-800 text-slate-300 text-xs rounded"
                >
                  Cancelar
                </button>
                <button type="submit" className="px-4 py-2 bg-blue-600 text-white text-xs rounded font-semibold">
                  Guardar
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}

ReactDOM.createRoot(document.getElementById('root')).render(<App />);