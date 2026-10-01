# React.js Pagination Guide for Roles API

Complete guide for implementing pagination in React.js when listing roles using the `list_roles` API endpoint.

## API Endpoint

**Endpoint:** `list_roles`  
**Method:** `POST`  
**Base URL:** `/api/method/techsavanna_pos.api.role_api.list_roles`

### Request Parameters

```typescript
interface ListRolesParams {
  disabled?: boolean;
  is_custom?: boolean;
  desk_access?: boolean;
  restrict_to_domain?: string;
  page?: number;        // Default: 1
  page_size?: number;   // Default: 20
}
```

### Response Structure

```typescript
interface ListRolesResponse {
  success: true;
  data: {
    roles: Array<{
      name: string;
      role_name: string;
      disabled: number;
      is_custom: number;
      desk_access: number;
      two_factor_auth: number;
      restrict_to_domain: string | null;
      home_page: string | null;
      user_count: number;
    }>;
    pagination: {
      page: number;
      page_size: number;
      total: number;
      total_pages: number;
    };
  };
}
```

---

## Implementation Options

### Option 1: Custom Hook with Page-Based Pagination

```typescript
// hooks/useRolesPagination.ts
import { useState, useEffect, useCallback } from 'react';

interface Role {
  name: string;
  role_name: string;
  disabled: number;
  is_custom: number;
  desk_access: number;
  two_factor_auth: number;
  restrict_to_domain: string | null;
  home_page: string | null;
  user_count: number;
}

interface PaginationInfo {
  page: number;
  page_size: number;
  total: number;
  total_pages: number;
}

interface UseRolesPaginationOptions {
  pageSize?: number;
  disabled?: boolean;
  is_custom?: boolean;
  desk_access?: boolean;
  restrict_to_domain?: string;
}

interface UseRolesPaginationReturn {
  roles: Role[];
  pagination: PaginationInfo;
  loading: boolean;
  error: string | null;
  goToPage: (page: number) => void;
  nextPage: () => void;
  previousPage: () => void;
  refresh: () => void;
  hasNextPage: boolean;
  hasPreviousPage: boolean;
}

export const useRolesPagination = (
  options: UseRolesPaginationOptions = {}
): UseRolesPaginationReturn => {
  const {
    pageSize = 20,
    disabled,
    is_custom,
    desk_access,
    restrict_to_domain,
  } = options;

  const [roles, setRoles] = useState<Role[]>([]);
  const [pagination, setPagination] = useState<PaginationInfo>({
    page: 1,
    page_size: pageSize,
    total: 0,
    total_pages: 0,
  });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchRoles = useCallback(
    async (page: number) => {
      setLoading(true);
      setError(null);

      try {
        const response = await fetch(
          '/api/method/techsavanna_pos.api.role_api.list_roles',
          {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
            },
            body: JSON.stringify({
              page,
              page_size: pageSize,
              disabled,
              is_custom,
              desk_access,
              restrict_to_domain,
            }),
          }
        );

        const data = await response.json();

        if (!data.success) {
          throw new Error(data.message || 'Failed to fetch roles');
        }

        setRoles(data.data.roles);
        setPagination(data.data.pagination);
      } catch (err: any) {
        setError(err.message || 'An error occurred');
        setRoles([]);
      } finally {
        setLoading(false);
      }
    },
    [pageSize, disabled, is_custom, desk_access, restrict_to_domain]
  );

  useEffect(() => {
    fetchRoles(1);
  }, [fetchRoles]);

  const goToPage = useCallback(
    (page: number) => {
      if (page >= 1 && page <= pagination.total_pages) {
        fetchRoles(page);
      }
    },
    [fetchRoles, pagination.total_pages]
  );

  const nextPage = useCallback(() => {
    if (pagination.page < pagination.total_pages) {
      goToPage(pagination.page + 1);
    }
  }, [pagination.page, pagination.total_pages, goToPage]);

  const previousPage = useCallback(() => {
    if (pagination.page > 1) {
      goToPage(pagination.page - 1);
    }
  }, [pagination.page, goToPage]);

  const refresh = useCallback(() => {
    fetchRoles(pagination.page);
  }, [fetchRoles, pagination.page]);

  return {
    roles,
    pagination,
    loading,
    error,
    goToPage,
    nextPage,
    previousPage,
    refresh,
    hasNextPage: pagination.page < pagination.total_pages,
    hasPreviousPage: pagination.page > 1,
  };
};
```

### Option 2: Component with Pagination Controls

```typescript
// components/RolesList.tsx
import React from 'react';
import { useRolesPagination } from '../hooks/useRolesPagination';

interface RolesListProps {
  pageSize?: number;
  filters?: {
    disabled?: boolean;
    is_custom?: boolean;
    desk_access?: boolean;
    restrict_to_domain?: string;
  };
}

export const RolesList: React.FC<RolesListProps> = ({
  pageSize = 20,
  filters = {},
}) => {
  const {
    roles,
    pagination,
    loading,
    error,
    goToPage,
    nextPage,
    previousPage,
    refresh,
    hasNextPage,
    hasPreviousPage,
  } = useRolesPagination({
    pageSize,
    ...filters,
  });

  if (loading && roles.length === 0) {
    return <div className="loading">Loading roles...</div>;
  }

  if (error) {
    return (
      <div className="error">
        <p>Error: {error}</p>
        <button onClick={refresh}>Retry</button>
      </div>
    );
  }

  return (
    <div className="roles-list">
      <div className="roles-header">
        <h2>Roles ({pagination.total})</h2>
        <button onClick={refresh} disabled={loading}>
          Refresh
        </button>
      </div>

      <table className="roles-table">
        <thead>
          <tr>
            <th>Role Name</th>
            <th>Users</th>
            <th>Custom</th>
            <th>Desk Access</th>
            <th>Status</th>
          </tr>
        </thead>
        <tbody>
          {roles.map((role) => (
            <tr key={role.name}>
              <td>{role.role_name}</td>
              <td>{role.user_count}</td>
              <td>{role.is_custom ? 'Yes' : 'No'}</td>
              <td>{role.desk_access ? 'Yes' : 'No'}</td>
              <td>{role.disabled ? 'Disabled' : 'Active'}</td>
            </tr>
          ))}
        </tbody>
      </table>

      {/* Pagination Controls */}
      <div className="pagination">
        <button
          onClick={previousPage}
          disabled={!hasPreviousPage || loading}
        >
          Previous
        </button>

        <span className="page-info">
          Page {pagination.page} of {pagination.total_pages} (
          {pagination.total} total)
        </span>

        <button onClick={nextPage} disabled={!hasNextPage || loading}>
          Next
        </button>
      </div>
    </div>
  );
};
```

### Option 3: Advanced Pagination with Page Numbers

```typescript
// components/AdvancedPagination.tsx
import React from 'react';

interface PaginationProps {
  currentPage: number;
  totalPages: number;
  onPageChange: (page: number) => void;
  loading?: boolean;
  maxVisiblePages?: number;
}

export const AdvancedPagination: React.FC<PaginationProps> = ({
  currentPage,
  totalPages,
  onPageChange,
  loading = false,
  maxVisiblePages = 5,
}) => {
  const getPageNumbers = () => {
    const pages: (number | string)[] = [];
    const halfVisible = Math.floor(maxVisiblePages / 2);

    let startPage = Math.max(1, currentPage - halfVisible);
    let endPage = Math.min(totalPages, currentPage + halfVisible);

    // Adjust if we're near the start or end
    if (currentPage <= halfVisible) {
      endPage = Math.min(maxVisiblePages, totalPages);
    }
    if (currentPage + halfVisible >= totalPages) {
      startPage = Math.max(1, totalPages - maxVisiblePages + 1);
    }

    // Add first page and ellipsis
    if (startPage > 1) {
      pages.push(1);
      if (startPage > 2) {
        pages.push('...');
      }
    }

    // Add page numbers
    for (let i = startPage; i <= endPage; i++) {
      pages.push(i);
    }

    // Add last page and ellipsis
    if (endPage < totalPages) {
      if (endPage < totalPages - 1) {
        pages.push('...');
      }
      pages.push(totalPages);
    }

    return pages;
  };

  if (totalPages <= 1) {
    return null;
  }

  return (
    <div className="advanced-pagination">
      <button
        onClick={() => onPageChange(1)}
        disabled={currentPage === 1 || loading}
        className="pagination-btn"
      >
        First
      </button>

      <button
        onClick={() => onPageChange(currentPage - 1)}
        disabled={currentPage === 1 || loading}
        className="pagination-btn"
      >
        Previous
      </button>

      {getPageNumbers().map((page, index) => {
        if (page === '...') {
          return (
            <span key={`ellipsis-${index}`} className="pagination-ellipsis">
              ...
            </span>
          );
        }

        return (
          <button
            key={page}
            onClick={() => onPageChange(page as number)}
            disabled={loading}
            className={`pagination-btn ${
              currentPage === page ? 'active' : ''
            }`}
          >
            {page}
          </button>
        );
      })}

      <button
        onClick={() => onPageChange(currentPage + 1)}
        disabled={currentPage === totalPages || loading}
        className="pagination-btn"
      >
        Next
      </button>

      <button
        onClick={() => onPageChange(totalPages)}
        disabled={currentPage === totalPages || loading}
        className="pagination-btn"
      >
        Last
      </button>
    </div>
  );
};
```

### Option 4: Infinite Scroll / Load More Pattern

```typescript
// hooks/useRolesInfiniteScroll.ts
import { useState, useEffect, useCallback, useRef } from 'react';

interface UseRolesInfiniteScrollOptions {
  pageSize?: number;
  disabled?: boolean;
  is_custom?: boolean;
  desk_access?: boolean;
  restrict_to_domain?: string;
}

export const useRolesInfiniteScroll = (
  options: UseRolesInfiniteScrollOptions = {}
) => {
  const {
    pageSize = 20,
    disabled,
    is_custom,
    desk_access,
    restrict_to_domain,
  } = options;

  const [roles, setRoles] = useState<Role[]>([]);
  const [currentPage, setCurrentPage] = useState(1);
  const [totalPages, setTotalPages] = useState(0);
  const [loading, setLoading] = useState(false);
  const [hasMore, setHasMore] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const loadMore = useCallback(async () => {
    if (loading || !hasMore) return;

    setLoading(true);
    setError(null);

    try {
      const response = await fetch(
        '/api/method/techsavanna_pos.api.role_api.list_roles',
        {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({
            page: currentPage + 1,
            page_size: pageSize,
            disabled,
            is_custom,
            desk_access,
            restrict_to_domain,
          }),
        }
      );

      const data = await response.json();

      if (!data.success) {
        throw new Error(data.message || 'Failed to fetch roles');
      }

      const newRoles = data.data.roles;
      setRoles((prev) => [...prev, ...newRoles]);
      setCurrentPage(data.data.pagination.page);
      setTotalPages(data.data.pagination.total_pages);
      setHasMore(data.data.pagination.page < data.data.pagination.total_pages);
    } catch (err: any) {
      setError(err.message || 'An error occurred');
    } finally {
      setLoading(false);
    }
  }, [
    currentPage,
    pageSize,
    disabled,
    is_custom,
    desk_access,
    restrict_to_domain,
    loading,
    hasMore,
  ]);

  const reset = useCallback(async () => {
    setRoles([]);
    setCurrentPage(0);
    setHasMore(true);
    setError(null);
    // Trigger initial load
    await loadMore();
  }, [loadMore]);

  useEffect(() => {
    reset();
  }, [disabled, is_custom, desk_access, restrict_to_domain]);

  return {
    roles,
    loadMore,
    loading,
    hasMore,
    error,
    reset,
  };
};

// Component using infinite scroll
export const RolesListInfinite: React.FC = () => {
  const { roles, loadMore, loading, hasMore, error } = useRolesInfiniteScroll();
  const observerTarget = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries[0].isIntersecting && hasMore && !loading) {
          loadMore();
        }
      },
      { threshold: 0.1 }
    );

    const currentTarget = observerTarget.current;
    if (currentTarget) {
      observer.observe(currentTarget);
    }

    return () => {
      if (currentTarget) {
        observer.unobserve(currentTarget);
      }
    };
  }, [hasMore, loading, loadMore]);

  return (
    <div className="roles-list-infinite">
      {roles.map((role) => (
        <div key={role.name} className="role-item">
          {role.role_name} ({role.user_count} users)
        </div>
      ))}

      {error && <div className="error">Error: {error}</div>}

      <div ref={observerTarget} className="load-more-trigger">
        {loading && <div>Loading more...</div>}
        {!hasMore && <div>No more roles to load</div>}
      </div>
    </div>
  );
};
```

### Option 5: Complete Example with Filters

```typescript
// components/RolesListWithFilters.tsx
import React, { useState } from 'react';
import { useRolesPagination } from '../hooks/useRolesPagination';
import { AdvancedPagination } from './AdvancedPagination';

export const RolesListWithFilters: React.FC = () => {
  const [filters, setFilters] = useState({
    disabled: undefined as boolean | undefined,
    is_custom: undefined as boolean | undefined,
    desk_access: undefined as boolean | undefined,
    restrict_to_domain: undefined as string | undefined,
  });

  const [pageSize, setPageSize] = useState(20);

  const {
    roles,
    pagination,
    loading,
    error,
    goToPage,
    refresh,
  } = useRolesPagination({
    pageSize,
    ...filters,
  });

  const handleFilterChange = (key: string, value: any) => {
    setFilters((prev) => ({
      ...prev,
      [key]: value === '' ? undefined : value,
    }));
    // Reset to page 1 when filters change
    goToPage(1);
  };

  return (
    <div className="roles-list-with-filters">
      <div className="filters">
        <select
          value={filters.disabled ?? ''}
          onChange={(e) =>
            handleFilterChange(
              'disabled',
              e.target.value === '' ? undefined : e.target.value === 'true'
            )
          }
        >
          <option value="">All Statuses</option>
          <option value="false">Active</option>
          <option value="true">Disabled</option>
        </select>

        <select
          value={filters.is_custom ?? ''}
          onChange={(e) =>
            handleFilterChange(
              'is_custom',
              e.target.value === '' ? undefined : e.target.value === 'true'
            )
          }
        >
          <option value="">All Types</option>
          <option value="true">Custom</option>
          <option value="false">Standard</option>
        </select>

        <select
          value={pageSize}
          onChange={(e) => {
            setPageSize(Number(e.target.value));
            goToPage(1);
          }}
        >
          <option value="10">10 per page</option>
          <option value="20">20 per page</option>
          <option value="50">50 per page</option>
          <option value="100">100 per page</option>
        </select>
      </div>

      {error && <div className="error">Error: {error}</div>}

      {loading && roles.length === 0 ? (
        <div>Loading...</div>
      ) : (
        <>
          <table>
            <thead>
              <tr>
                <th>Role Name</th>
                <th>Users</th>
                <th>Type</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {roles.map((role) => (
                <tr key={role.name}>
                  <td>{role.role_name}</td>
                  <td>{role.user_count}</td>
                  <td>{role.is_custom ? 'Custom' : 'Standard'}</td>
                  <td>{role.disabled ? 'Disabled' : 'Active'}</td>
                </tr>
              ))}
            </tbody>
          </table>

          <AdvancedPagination
            currentPage={pagination.page}
            totalPages={pagination.total_pages}
            onPageChange={goToPage}
            loading={loading}
          />
        </>
      )}
    </div>
  );
};
```

---

## Best Practices

### 1. **Debounce Filter Changes**

```typescript
import { useDebounce } from './hooks/useDebounce';

const [searchTerm, setSearchTerm] = useState('');
const debouncedSearchTerm = useDebounce(searchTerm, 500);

// Use debouncedSearchTerm in API call
```

### 2. **Cache Previous Results**

```typescript
const [cache, setCache] = useState<Map<string, Role[]>>(new Map());

const fetchRoles = async (page: number) => {
  const cacheKey = `${page}-${JSON.stringify(filters)}`;
  
  if (cache.has(cacheKey)) {
    setRoles(cache.get(cacheKey)!);
    return;
  }
  
  // Fetch from API and cache result
};
```

### 3. **Optimistic UI Updates**

```typescript
const goToPage = (page: number) => {
  // Show loading state immediately
  setLoading(true);
  
  // Update pagination optimistically
  setPagination((prev) => ({ ...prev, page }));
  
  // Then fetch actual data
  fetchRoles(page);
};
```

### 4. **Error Retry Logic**

```typescript
const [retryCount, setRetryCount] = useState(0);
const MAX_RETRIES = 3;

const fetchRoles = async (page: number, retry = 0) => {
  try {
    // ... fetch logic
  } catch (err) {
    if (retry < MAX_RETRIES) {
      setTimeout(() => fetchRoles(page, retry + 1), 1000 * (retry + 1));
    } else {
      setError('Failed to load roles after multiple attempts');
    }
  }
};
```

### 5. **URL State Management**

```typescript
import { useSearchParams } from 'react-router-dom';

const [searchParams, setSearchParams] = useSearchParams();
const page = Number(searchParams.get('page')) || 1;

const goToPage = (newPage: number) => {
  setSearchParams({ page: newPage.toString() });
  fetchRoles(newPage);
};
```

---

## CSS Styling Example

```css
.pagination {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 0.5rem;
  margin-top: 2rem;
}

.pagination-btn {
  padding: 0.5rem 1rem;
  border: 1px solid #ddd;
  background: white;
  cursor: pointer;
  border-radius: 4px;
}

.pagination-btn:hover:not(:disabled) {
  background: #f5f5f5;
}

.pagination-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.pagination-btn.active {
  background: #007bff;
  color: white;
  border-color: #007bff;
}

.page-info {
  margin: 0 1rem;
  color: #666;
}
```

---

## Summary

1. **Page-based pagination**: Best for most use cases, provides clear navigation
2. **Infinite scroll**: Good for mobile or when you want continuous scrolling
3. **Load more button**: Simple alternative to infinite scroll
4. **Always show loading states**: Improve UX during data fetching
5. **Handle errors gracefully**: Provide retry mechanisms
6. **Cache when appropriate**: Reduce unnecessary API calls
7. **URL state management**: Allow bookmarking and sharing of specific pages

Choose the pattern that best fits your application's needs and user experience requirements.

