<template>
  <div class="dashboard-layout">
    <div class="dashboard-content">
      <!-- 筛选栏 -->
      <div class="filter-section">
        <n-space align="center" :size="12">
          <n-date-picker
            v-model:value="dateRange"
            type="daterange"
            clearable
            size="small"
            @update:value="handleDateChange"
          />
          <n-select
            v-model:value="sortField"
            :options="sortOptions"
            size="small"
            style="width: 140px"
            @update:value="fetchData"
          />
          <n-button size="small" type="info" @click="fetchData">查询</n-button>
        </n-space>
      </div>

      <!-- 数据表格 -->
      <n-card :bordered="false" class="table-card">
        <template #header>
          <div class="card-header">
            <span class="card-title">素材消耗排行榜</span>
            <span class="card-subtitle">共 {{ totalCount }} 条</span>
          </div>
        </template>

        <n-data-table
          :columns="columns"
          :data="tableData"
          :loading="loading"
          :pagination="false"
          :bordered="false"
          striped
          size="small"
          :max-height="600"
        />

        <div class="table-footer">
          <n-pagination
            v-model:page="page"
            v-model:page-size="pageSize"
            :item-count="totalCount"
            :page-size-options="[10, 20, 50, 100]"
            size="small"
            show-quick-jumper
            show-size-picker
            @update:page="fetchData"
            @update:page-size="handlePageSizeChange"
          />
        </div>
      </n-card>
    </div>
  </div>
</template>

<script setup lang="ts">
import { h, ref, computed, onMounted } from 'vue'
import {
  NCard, NDatePicker, NSelect, NButton, NSpace,
  NDataTable, NPagination, NTag, useMessage
} from 'naive-ui'
import type { DataTableColumns } from 'naive-ui'
import { materialsReportApi } from '../api/report'

const message = useMessage()
const loading = ref(false)
const tableData = ref<any[]>([])
const totalCount = ref(0)
const page = ref(1)
const pageSize = ref(20)
const sortField = ref('cost')
const dateRange = ref<[number, number] | null>(null)

const sortOptions = [
  { label: '按花费排序', value: 'cost' },
  { label: '按展示排序', value: 'show_count' },
  { label: '按点击排序', value: 'click' },
  { label: '按转化排序', value: 'conversion_num' },
]

function formatDate(d: Date) {
  return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`
}

function fmtNum(v: any) {
  const n = Number(v) || 0
  if (n >= 10000) return (n / 10000).toFixed(1) + 'w'
  return n.toLocaleString()
}

function fmtMoney(v: any) {
  return '¥' + Number(v || 0).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

function fmtPct(v: any) {
  if (!v) return '-'
  return String(v).replace('%', '') + '%'
}

const columns: DataTableColumns<any> = [
  { title: '排名', key: 'rank', width: 60, align: 'center',
    render: (_, i) => h('span', { class: 'rank-num' }, (page.value - 1) * pageSize.value + i + 1) },
  { title: '素材名称', key: 'material_name', ellipsis: true, minWidth: 200 },
  { title: '类型', key: 'material_type_name', width: 70, align: 'center' },
  { title: '花费', key: 'cost', width: 100, align: 'right',
    render: (row) => h('span', { class: 'money' }, fmtMoney(row.cost)) },
  { title: '展示', key: 'show_count', width: 90, align: 'right',
    render: (row) => fmtNum(row.show_count) },
  { title: '点击', key: 'click', width: 80, align: 'right',
    render: (row) => fmtNum(row.click) },
  { title: 'CTR', key: 'ctr', width: 70, align: 'right',
    render: (row) => fmtPct(row.ctr) },
  { title: 'CPM', key: 'cpm', width: 70, align: 'right',
    render: (row) => '¥' + (row.cpm || 0) },
  { title: 'CPC', key: 'cpc', width: 70, align: 'right',
    render: (row) => '¥' + (row.cpc || 0) },
  { title: '转化数', key: 'conversion_num', width: 80, align: 'right',
    render: (row) => fmtNum(row.conversion_num) },
  { title: '转化率', key: 'convert_rate', width: 75, align: 'right',
    render: (row) => fmtPct(row.convert_rate) },
  { title: '转化成本', key: 'convert_cost', width: 90, align: 'right',
    render: (row) => fmtMoney(row.convert_cost) },
  { title: '激活', key: 'active', width: 70, align: 'right',
    render: (row) => fmtNum(row.active) },
  { title: '激活成本', key: 'active_cost', width: 90, align: 'right',
    render: (row) => fmtMoney(row.active_cost) },
]

async function fetchData() {
  loading.value = true
  try {
    const params: any = {
      sort_field: sortField.value,
      sort_direction: 'desc',
      page: page.value,
      page_size: pageSize.value,
    }
    if (dateRange.value) {
      const start = new Date(dateRange.value[0])
      const end = new Date(dateRange.value[1])
      params.start_date = formatDate(start)
      params.end_date = formatDate(end)
    }
    const res = await materialsReportApi(params)
    if (res.code === 0) {
      tableData.value = res.data?.list || []
      totalCount.value = res.data?.page_info?.total_count || 0
    } else {
      message.error(res.msg || '加载失败')
    }
  } catch (err: any) {
    message.error(err.message || '请求失败')
  } finally {
    loading.value = false
  }
}

function handleDateChange() {
  page.value = 1
  fetchData()
}

function handlePageSizeChange() {
  page.value = 1
  fetchData()
}

onMounted(() => {
  // 默认查今天
  const now = Date.now()
  dateRange.value = [now, now]
  fetchData()
})
</script>

<style scoped>
.dashboard-layout {
  min-height: 100vh;
  background: var(--bg-primary, #0a0a0f);
}
.dashboard-content {
  padding: 24px;
}
.filter-section {
  margin-bottom: 16px;
  padding: 16px;
  background: var(--bg-secondary, #141418);
  border-radius: 10px;
  border: 1px solid var(--border-color, #2a2a35);
}
.table-card {
  background: var(--bg-secondary, #141418);
  border: 1px solid var(--border-color, #2a2a35);
  border-radius: 10px;
}
.card-header {
  display: flex;
  align-items: center;
  gap: 12px;
}
.card-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--text-primary, #e0e0e5);
}
.card-subtitle {
  font-size: 12px;
  color: var(--text-tertiary, #666);
}
.table-footer {
  display: flex;
  justify-content: flex-end;
  padding-top: 12px;
  border-top: 1px solid var(--border-color, #2a2a35);
  margin-top: 8px;
}
.rank-num {
  color: #6366F1;
  font-weight: 600;
}
.money {
  color: #F59E0B;
  font-weight: 600;
}
</style>
