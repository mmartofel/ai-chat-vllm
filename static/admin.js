const { createApp, ref, onMounted } = Vue;

createApp({
    setup() {
        const authState = ref('checking');   // 'checking' | 'ok' | 'denied'
        const activeTab = ref('users');
        const currentUsername = ref('');

        const users = ref([]);
        const roles = ref([]);
        const allPermissions = ['chat', 'manage_users', 'manage_roles', 'moderate_content'];

        const showCreateUser = ref(false);
        const newUser = ref({ username: '', password: '', role_name: 'user', is_active: true });

        const showCreateRole = ref(false);
        const newRole = ref({ name: '', permissions: [] });

        const modal = ref({ open: false, type: '', data: {} });
        const toast = ref({ msg: '' });

        // --- API helper ---

        const apiFetch = async (url, opts = {}) => {
            const res = await fetch(url, {
                headers: { 'Content-Type': 'application/json' },
                ...opts
            });
            if (res.status === 401) { window.location.href = '/'; return null; }
            if (res.status === 204) return null;
            const data = await res.json();
            if (!res.ok) throw new Error(data.detail || 'Request failed');
            return data;
        };

        const showToast = (msg) => {
            toast.value.msg = msg;
            setTimeout(() => { toast.value.msg = ''; }, 2500);
        };

        const formatDate = (iso) => {
            if (!iso) return '';
            return new Date(iso).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
        };

        // --- Users ---

        const loadUsers = async () => {
            try {
                users.value = await apiFetch('/admin/users') ?? [];
            } catch (e) {
                showToast('Failed to load users: ' + e.message);
            }
        };

        const createUser = async () => {
            try {
                await apiFetch('/admin/users', {
                    method: 'POST',
                    body: JSON.stringify(newUser.value)
                });
                showToast('User created');
                newUser.value = { username: '', password: '', role_name: 'user', is_active: true };
                showCreateUser.value = false;
                await loadUsers();
            } catch (e) {
                showToast('Error: ' + e.message);
            }
        };

        const openEditUser = (u) => {
            modal.value = {
                open: true,
                type: 'user',
                data: { id: u.id, username: u.username, role_name: u.role_name, is_active: u.is_active }
            };
        };

        const saveEditUser = async () => {
            try {
                await apiFetch(`/admin/users/${modal.value.data.id}`, {
                    method: 'PUT',
                    body: JSON.stringify({
                        role_name: modal.value.data.role_name,
                        is_active: modal.value.data.is_active
                    })
                });
                showToast('User updated');
                modal.value.open = false;
                await loadUsers();
            } catch (e) {
                showToast('Error: ' + e.message);
            }
        };

        const deleteUser = async (u) => {
            if (!confirm(`Delete user "${u.username}"?`)) return;
            try {
                await apiFetch(`/admin/users/${u.id}`, { method: 'DELETE' });
                showToast('User deleted');
                await loadUsers();
            } catch (e) {
                showToast('Error: ' + e.message);
            }
        };

        // --- Roles ---

        const loadRoles = async () => {
            try {
                roles.value = await apiFetch('/admin/roles') ?? [];
            } catch (e) {
                showToast('Failed to load roles: ' + e.message);
            }
        };

        const createRole = async () => {
            try {
                await apiFetch('/admin/roles', {
                    method: 'POST',
                    body: JSON.stringify(newRole.value)
                });
                showToast('Role created');
                newRole.value = { name: '', permissions: [] };
                showCreateRole.value = false;
                await loadRoles();
            } catch (e) {
                showToast('Error: ' + e.message);
            }
        };

        const openEditRole = (r) => {
            modal.value = {
                open: true,
                type: 'role',
                data: {
                    id: r.id,
                    name: r.name,
                    originalName: r.name,
                    permissions: [...r.permissions]
                }
            };
        };

        const saveEditRole = async () => {
            try {
                await apiFetch(`/admin/roles/${modal.value.data.id}`, {
                    method: 'PUT',
                    body: JSON.stringify({
                        name: modal.value.data.name,
                        permissions: modal.value.data.permissions
                    })
                });
                showToast('Role updated');
                modal.value.open = false;
                await loadRoles();
            } catch (e) {
                showToast('Error: ' + e.message);
            }
        };

        const deleteRole = async (r) => {
            if (!confirm(`Delete role "${r.name}"?`)) return;
            try {
                await apiFetch(`/admin/roles/${r.id}`, { method: 'DELETE' });
                showToast('Role deleted');
                await loadRoles();
            } catch (e) {
                showToast('Error: ' + e.message);
            }
        };

        // --- Init ---

        onMounted(async () => {
            try {
                const me = await apiFetch('/auth/me');
                if (!me || me.role !== 'admin') {
                    authState.value = 'denied';
                    return;
                }
                currentUsername.value = me.username;
                authState.value = 'ok';
                await Promise.all([loadUsers(), loadRoles()]);
            } catch (_) {
                authState.value = 'denied';
            }
        });

        return {
            authState, activeTab, currentUsername,
            users, roles, allPermissions,
            showCreateUser, newUser,
            showCreateRole, newRole,
            modal, toast,
            formatDate, showToast,
            loadUsers, createUser, openEditUser, saveEditUser, deleteUser,
            loadRoles, createRole, openEditRole, saveEditRole, deleteRole,
        };
    }
}).mount('#admin-app');
