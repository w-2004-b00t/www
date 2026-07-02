import { createRouter, createWebHistory } from 'vue-router'
import { useAuthStore } from '../stores/auth'
import StudentLayout from '../layouts/StudentLayout.vue'
import AdminLayout from '../layouts/AdminLayout.vue'
import DemoLayout from '../layouts/DemoLayout.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', redirect: '/student/dashboard' },
    { path: '/login', component: () => import('../views/login/LoginView.vue'), meta: { public: true } },
    {
      path: '/student',
      component: StudentLayout,
      meta: { roles: ['student', 'demo'] },
      children: [
        { path: 'onboarding', component: () => import('../views/student/OnboardingView.vue') },
        { path: 'dashboard', component: () => import('../views/student/DashboardView.vue') },
        { path: 'profile-chat', component: () => import('../views/student/ProfileChatView.vue') },
        { path: 'profile', component: () => import('../views/student/ProfileView.vue') },
        { path: 'resource-generate', component: () => import('../views/student/ResourceGenerateView.vue') },
        { path: 'resources', component: () => import('../views/student/ResourceCenterView.vue') },
        { path: 'resources/:id', component: () => import('../views/student/ResourceDetailView.vue') },
        { path: 'mindmap', component: () => import('../views/student/MindMapShowcaseView.vue') },
        { path: 'knowledge-graph', component: () => import('../views/student/KnowledgeGraphView.vue') },
        { path: 'video-demo', component: () => import('../views/student/VideoDemoView.vue') },
        { path: 'learning-path', component: () => import('../views/student/LearningPathView.vue') },
        {
          path: 'tutor',
          redirect: {
            path: '/student/learning-path',
            query: { tutor: 'open' },
          },
        },
        { path: 'assessment', component: () => import('../views/student/AssessmentView.vue') },
        { path: 'report', component: () => import('../views/student/LearningReportView.vue') },
        { path: 'mistakes', component: () => import('../views/student/MistakeBookView.vue') },
      ],
    },
    {
      path: '/admin',
      component: AdminLayout,
      meta: { roles: ['teacher', 'admin'] },
      children: [
        { path: 'dashboard', component: () => import('../views/admin/AdminDashboardView.vue') },
        { path: 'courses', component: () => import('../views/admin/CourseManageView.vue') },
        { path: 'documents', component: () => import('../views/admin/DocumentManageView.vue') },
        { path: 'tasks', component: () => import('../views/admin/TaskManageView.vue') },
        { path: 'audit', component: () => import('../views/admin/ResourceAuditView.vue') },
        { path: 'analytics', component: () => import('../views/admin/StudentAnalysisView.vue') },
        { path: 'model-config', component: () => import('../views/admin/ModelConfigView.vue') },
      ],
    },
    {
      path: '/demo',
      component: DemoLayout,
      meta: { public: true },
      children: [
        { path: '', component: () => import('../views/demo/DemoHomeView.vue') },
        { path: 'flow', component: () => import('../views/demo/DemoFlowView.vue') },
      ],
    },
  ],
})

router.beforeEach((to) => {
  const auth = useAuthStore()
  if (to.meta.public) return true
  if (!auth.isLoggedIn) return '/login'
  const roles = to.meta.roles as string[] | undefined
  if (roles && auth.role && !roles.includes(auth.role)) {
    return auth.role === 'student' ? '/student/dashboard' : '/admin/dashboard'
  }
  return true
})

export default router
