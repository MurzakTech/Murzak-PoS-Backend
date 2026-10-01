# Frontend RBAC Implementation Guide

This guide explains how to use the business capabilities RBAC system in your frontend application.

## API Response Structure

The `get_current_user` endpoint returns business capabilities in three optimized formats:

```typescript
{
  "business_capabilities": {
    // List format - Array of capabilities with actions
    "list": [
      {
        "capability": "manage_customers",
        "actions": ["create", "edit", "view", "delete"]
      },
      {
        "capability": "process_sales",
        "actions": ["create", "edit", "submit", "cancel", "view", "print"]
      }
    ],
    
    // Map format - Object mapping capability -> actions array
    "map": {
      "manage_customers": ["create", "edit", "view", "delete"],
      "process_sales": ["create", "edit", "submit", "cancel", "view", "print"]
    },
    
    // Flattened format - Direct permission checks
    "permissions": {
      "manage_customers:create": true,
      "manage_customers:edit": true,
      "manage_customers:view": true,
      "manage_customers:delete": true,
      "process_sales:create": true,
      "process_sales:edit": true,
      "process_sales:submit": true,
      "process_sales:cancel": true,
      "process_sales:view": true,
      "process_sales:print": true
    }
  }
}
```

## Business Capabilities

Available capabilities and their typical actions:

- **manage_customers**: create, edit, view, delete, export, import
- **manage_products**: create, edit, view, delete, export, import
- **process_sales**: create, edit, view, submit, cancel, amend, print, email, export
- **manage_purchases**: create, edit, view, submit, cancel, print, export
- **manage_inventory**: create, edit, view, submit, cancel, print, export
- **manage_finances**: create, edit, view, submit, cancel, print, export
- **view_reports**: view, export, print
- **manage_users**: create, edit, view, delete
- **manage_staff**: create, edit, view, delete
- **manage_company**: edit, view
- **manage_settings**: edit, view

## Frontend Implementation Examples

### 1. Permission Utility/Helper (Recommended)

Create a permission utility that provides clean, type-safe methods:

```typescript
// utils/permissions.ts
interface BusinessCapabilities {
  list: Array<{ capability: string; actions: string[] }>;
  map: Record<string, string[]>;
  permissions: Record<string, boolean>;
}

class PermissionManager {
  private capabilities: BusinessCapabilities;

  constructor(capabilities: BusinessCapabilities) {
    this.capabilities = capabilities;
  }

  /**
   * Check if user has a specific permission
   * @param capability - The business capability (e.g., 'manage_customers')
   * @param action - The action (e.g., 'create', 'edit', 'view')
   * @returns true if user has the permission
   */
  can(capability: string, action: string): boolean {
    const permissionKey = `${capability}:${action}`;
    return this.capabilities.permissions[permissionKey] === true;
  }

  /**
   * Check if user has ANY of the specified permissions
   * @param checks - Array of [capability, action] pairs
   * @returns true if user has at least one permission
   */
  canAny(checks: Array<[string, string]>): boolean {
    return checks.some(([capability, action]) => this.can(capability, action));
  }

  /**
   * Check if user has ALL of the specified permissions
   * @param checks - Array of [capability, action] pairs
   * @returns true if user has all permissions
   */
  canAll(checks: Array<[string, string]>): boolean {
    return checks.every(([capability, action]) => this.can(capability, action));
  }

  /**
   * Get all actions for a capability
   * @param capability - The business capability
   * @returns Array of allowed actions
   */
  getActions(capability: string): string[] {
    return this.capabilities.map[capability] || [];
  }

  /**
   * Check if user has access to a capability (any action)
   * @param capability - The business capability
   * @returns true if user has any action for this capability
   */
  hasCapability(capability: string): boolean {
    const actions = this.capabilities.map[capability];
    return actions ? actions.length > 0 : false;
  }

  /**
   * Get all capabilities the user has access to
   * @returns Array of capability names
   */
  getCapabilities(): string[] {
    return Object.keys(this.capabilities.map);
  }

  /**
   * Filter items based on capability
   * @param items - Array of items with required permissions
   * @param getPermission - Function to extract permission from item
   * @returns Filtered array of items user can access
   */
  filterByPermission<T>(
    items: T[],
    getPermission: (item: T) => [string, string] | null
  ): T[] {
    return items.filter(item => {
      const permission = getPermission(item);
      return permission ? this.can(...permission) : true;
    });
  }
}

// Usage
export const usePermissions = () => {
  // Get from your auth context/store
  const capabilities = user.business_capabilities;
  return new PermissionManager(capabilities);
};
```

### 2. React Hook Example

```typescript
// hooks/usePermissions.ts
import { useAuth } from './useAuth';

export const usePermissions = () => {
  const { user } = useAuth();
  const capabilities = user?.business_capabilities || {
    list: [],
    map: {},
    permissions: {}
  };

  const can = (capability: string, action: string): boolean => {
    const permissionKey = `${capability}:${action}`;
    return capabilities.permissions[permissionKey] === true;
  };

  const hasCapability = (capability: string): boolean => {
    return !!capabilities.map[capability]?.length;
  };

  const getActions = (capability: string): string[] => {
    return capabilities.map[capability] || [];
  };

  return { can, hasCapability, getActions, capabilities };
};
```

### 3. Route Guards / Navigation Guards

```typescript
// Vue Router Example
router.beforeEach((to, from, next) => {
  const { capabilities } = useAuth();
  
  // Get required permission from route meta
  const requiredPermission = to.meta.permission as [string, string] | undefined;
  
  if (requiredPermission) {
    const [capability, action] = requiredPermission;
    const permissionKey = `${capability}:${action}`;
    
    if (capabilities.permissions[permissionKey]) {
      next(); // User has permission
    } else {
      next({ path: '/unauthorized' }); // Redirect to unauthorized page
    }
  } else {
    next(); // No permission required
  }
});

// React Router Example
const ProtectedRoute = ({ 
  children, 
  requiredPermission 
}: { 
  children: React.ReactNode;
  requiredPermission: [string, string];
}) => {
  const { can } = usePermissions();
  const [capability, action] = requiredPermission;

  if (can(capability, action)) {
    return <>{children}</>;
  }

  return <Navigate to="/unauthorized" />;
};
```

### 4. Component-Level Permission Checks

```typescript
// React Component Example
const CustomerList = () => {
  const { can, hasCapability } = usePermissions();
  const canCreate = can('manage_customers', 'create');
  const canEdit = can('manage_customers', 'edit');
  const canDelete = can('manage_customers', 'delete');

  return (
    <div>
      <h1>Customers</h1>
      
      {canCreate && (
        <button onClick={handleCreate}>Add Customer</button>
      )}
      
      <table>
        {customers.map(customer => (
          <tr key={customer.id}>
            <td>{customer.name}</td>
            <td>
              {canEdit && (
                <button onClick={() => handleEdit(customer)}>Edit</button>
              )}
              {canDelete && (
                <button onClick={() => handleDelete(customer)}>Delete</button>
              )}
            </td>
          </tr>
        ))}
      </table>
    </div>
  );
};
```

### 5. Menu/Navigation Filtering

```typescript
// Filter menu items based on capabilities
const menuItems = [
  {
    label: 'Customers',
    path: '/customers',
    requiredPermission: ['manage_customers', 'view'] as [string, string]
  },
  {
    label: 'Sales',
    path: '/sales',
    requiredPermission: ['process_sales', 'view'] as [string, string]
  },
  {
    label: 'Reports',
    path: '/reports',
    requiredPermission: ['view_reports', 'view'] as [string, string]
  }
];

const getVisibleMenuItems = (capabilities: BusinessCapabilities) => {
  return menuItems.filter(item => {
    const [capability, action] = item.requiredPermission;
    const permissionKey = `${capability}:${action}`;
    return capabilities.permissions[permissionKey] === true;
  });
};
```

### 6. Button/Action Visibility

```typescript
// Conditional rendering based on permissions
const SalesInvoiceActions = ({ invoice }) => {
  const { can } = usePermissions();
  
  const canEdit = can('process_sales', 'edit');
  const canSubmit = can('process_sales', 'submit');
  const canCancel = can('process_sales', 'cancel');
  const canPrint = can('process_sales', 'print');
  const canEmail = can('process_sales', 'email');

  return (
    <div className="actions">
      {canEdit && <Button onClick={handleEdit}>Edit</Button>}
      {canSubmit && invoice.status === 'Draft' && (
        <Button onClick={handleSubmit}>Submit</Button>
      )}
      {canCancel && invoice.status === 'Submitted' && (
        <Button onClick={handleCancel}>Cancel</Button>
      )}
      {canPrint && <Button onClick={handlePrint}>Print</Button>}
      {canEmail && <Button onClick={handleEmail}>Email</Button>}
    </div>
  );
};
```

### 7. API Request Interception

```typescript
// Add permission checks before API calls
const apiClient = axios.create();

apiClient.interceptors.request.use((config) => {
  const { capabilities } = useAuth();
  
  // Get required permission from request metadata
  const requiredPermission = config.meta?.permission as [string, string];
  
  if (requiredPermission) {
    const [capability, action] = requiredPermission;
    const permissionKey = `${capability}:${action}`;
    
    if (!capabilities.permissions[permissionKey]) {
      return Promise.reject({
        message: 'Insufficient permissions',
        code: 'PERMISSION_DENIED'
      });
    }
  }
  
  return config;
});
```

## Best Practices

1. **Centralize Permission Logic**: Create a single permission utility/hook to manage all permission checks
2. **Type Safety**: Use TypeScript to define capability and action types
3. **Cache Permissions**: Store permissions in your auth context/store to avoid repeated API calls
4. **Defensive Checks**: Always check if permissions exist before accessing them
5. **Graceful Degradation**: Hide/disable features rather than breaking the UI when permissions are missing
6. **Clear Error Messages**: Show helpful messages when users try to access restricted features
7. **Performance**: Use the `map` format for O(1) lookups, `permissions` for direct checks
8. **Test Permissions**: Write unit tests for permission checking logic

## TypeScript Type Definitions

```typescript
type Capability = 
  | 'manage_customers'
  | 'manage_products'
  | 'process_sales'
  | 'manage_purchases'
  | 'manage_inventory'
  | 'manage_finances'
  | 'view_reports'
  | 'manage_users'
  | 'manage_staff'
  | 'manage_company'
  | 'manage_settings';

type Action = 
  | 'view'
  | 'create'
  | 'edit'
  | 'delete'
  | 'submit'
  | 'cancel'
  | 'amend'
  | 'print'
  | 'export'
  | 'import'
  | 'email'
  | 'report';

interface BusinessCapabilities {
  list: Array<{ capability: Capability; actions: Action[] }>;
  map: Record<Capability, Action[]>;
  permissions: Record<`${Capability}:${Action}`, boolean>;
}
```

## Example: Complete Implementation

```typescript
// Store permissions in your auth store/context
const AuthContext = createContext<{
  user: User;
  permissions: PermissionManager;
}>();

// In your auth provider
const AuthProvider = ({ children }) => {
  const [user, setUser] = useState(null);
  
  useEffect(() => {
    // Fetch user data
    fetchCurrentUser().then(userData => {
      setUser(userData);
    });
  }, []);
  
  const permissions = useMemo(() => {
    if (!user?.business_capabilities) {
      return new PermissionManager({
        list: [],
        map: {},
        permissions: {}
      });
    }
    return new PermissionManager(user.business_capabilities);
  }, [user]);
  
  return (
    <AuthContext.Provider value={{ user, permissions }}>
      {children}
    </AuthContext.Provider>
  );
};

// Use throughout your app
const MyComponent = () => {
  const { permissions } = useAuth();
  
  if (!permissions.can('manage_customers', 'view')) {
    return <div>Access Denied</div>;
  }
  
  return <div>Customer Management</div>;
};
```

## Security Notes

⚠️ **Important**: Frontend permission checks are for UX only. Always validate permissions on the backend!

- Frontend checks prevent users from seeing/clicking restricted actions
- Backend validation ensures users cannot bypass frontend restrictions
- Never trust frontend permission checks alone
- Always implement server-side permission validation for all API endpoints

