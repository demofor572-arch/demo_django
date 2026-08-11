from rest_framework import serializers
from .models import Student, Group, Teacher, Course, News


class TeacherMiniSerializer(serializers.ModelSerializer):
    class Meta:
        model = Teacher
        fields = ["id", "name", "phone", "is_senior"]


class StudentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Student
        fields = ["id", "name", "surname", "phone", "teacher"]


class GroupSerializer(serializers.ModelSerializer):
    course_name = serializers.CharField(source="course.name", read_only=True)
    monthly_fee = serializers.DecimalField(
        source="course.monthly_fee", max_digits=12, decimal_places=2, read_only=True
    )

    students_count = serializers.SerializerMethodField()
    students = StudentSerializer(many=True, read_only=True)
    teacher = TeacherMiniSerializer(read_only=True)  # ✅ endi to'liq obyekt qaytaradi

    class Meta:
        model = Group
        fields = "__all__"

    def get_students_count(self, obj):
        return obj.students.count()


class CourseSerializer(serializers.ModelSerializer):
    groups_count = serializers.SerializerMethodField()

    class Meta:
        model = Course
        fields = ["id", "name", "monthly_fee", "groups_count"]

    def get_groups_count(self, obj):
        return obj.groups.count()


class NewsSerializer(serializers.ModelSerializer):
    created_by_name = serializers.CharField(
        source="created_by.get_full_name", read_only=True, default=""
    )

    class Meta:
        model = News
        fields = [
            "id",
            "title",
            "content",
            "priority",
            "is_active",
            "created_by_name",
            "created_at",
            "updated_at",
            "expires_at",
        ]
        read_only_fields = ["created_by_name", "created_at", "updated_at"]
